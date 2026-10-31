#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <linux/videodev2.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/file.h>
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
#include <time.h>
#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <stdexcept>
#include <vector>

static int64_t clock_ns(clockid_t id) {
  timespec t{};
  if (clock_gettime(id, &t)) throw std::runtime_error("clock_gettime failed");
  return int64_t(t.tv_sec) * 1000000000LL + t.tv_nsec;
}
struct Capture {
  int fd=-1;
  bool streaming=false;
  struct Buffer {void *data; size_t size;};
  std::vector<Buffer> buffers;
  void call(unsigned long request, void *arg) {
    int rc;
    do {rc=ioctl(fd, request, arg);} while(rc<0 && errno==EINTR);
    if(rc<0) throw std::runtime_error(std::string("V4L2 ioctl: ")+strerror(errno));
  }
  ~Capture() {
    if(streaming) {int type=V4L2_BUF_TYPE_VIDEO_CAPTURE; ioctl(fd, VIDIOC_STREAMOFF, &type);}
    for(auto &b:buffers) munmap(b.data,b.size);
    if(fd>=0) close(fd);
  }
};
static uint8_t clamp(int v) {return static_cast<uint8_t>(std::clamp(v,0,255));}

int main(int argc, char **argv) {
  rclcpp::init(argc,argv);
  auto node=std::make_shared<rclcpp::Node>("y1_wrist_camera");
  int result=0;
  try {
    if(node->get_parameter("use_sim_time").as_bool())
      throw std::runtime_error("Physical capture requires use_sim_time=false");
    const auto device=node->declare_parameter<std::string>("device",
      "/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._KD-USB_Cameras_KD-USB_Cameras-video-index0");
    const auto frame_id=node->declare_parameter<std::string>("frame_id","wrist_camera_optical_frame");
    const int width=node->declare_parameter<int>("width",640);
    const int height=node->declare_parameter<int>("height",480);
    const int fps=node->declare_parameter<int>("fps",25);
    if(width<=0 || height<=0 || width%2 || fps<1 || fps>30)
      throw std::runtime_error("Invalid width/height/fps (fps must be 1..30)");
    auto publisher=node->create_publisher<sensor_msgs::msg::Image>(
      "/wrist_camera/image_raw",rclcpp::SensorDataQoS().keep_last(5));
    Capture cap;
    cap.fd=open(device.c_str(),O_RDWR|O_NONBLOCK|O_CLOEXEC);
    if(cap.fd<0) throw std::runtime_error("Cannot open "+device+": "+strerror(errno));
    if(flock(cap.fd,LOCK_EX|LOCK_NB)) throw std::runtime_error("Camera already locked by another instance");
    v4l2_format fmt{}; fmt.type=V4L2_BUF_TYPE_VIDEO_CAPTURE;
    fmt.fmt.pix.width=width; fmt.fmt.pix.height=height;
    fmt.fmt.pix.pixelformat=V4L2_PIX_FMT_YUYV; fmt.fmt.pix.field=V4L2_FIELD_ANY;
    cap.call(VIDIOC_S_FMT,&fmt);
    if(fmt.fmt.pix.width!=unsigned(width) || fmt.fmt.pix.height!=unsigned(height) ||
       fmt.fmt.pix.pixelformat!=V4L2_PIX_FMT_YUYV || fmt.fmt.pix.bytesperline<unsigned(width*2))
      throw std::runtime_error("Camera did not accept requested YUYV dimensions/stride");
    v4l2_streamparm param{}; param.type=V4L2_BUF_TYPE_VIDEO_CAPTURE;
    param.parm.capture.timeperframe.numerator=1;
    param.parm.capture.timeperframe.denominator=fps;
    cap.call(VIDIOC_S_PARM,&param); cap.call(VIDIOC_G_PARM,&param);
    const auto period=param.parm.capture.timeperframe;
    if(!period.numerator || std::abs(double(period.denominator)/period.numerator-fps)>0.1)
      throw std::runtime_error("Camera did not accept requested FPS; use 640x480 at 25 FPS");
    v4l2_requestbuffers req{}; req.type=V4L2_BUF_TYPE_VIDEO_CAPTURE;
    req.memory=V4L2_MEMORY_MMAP; req.count=4; cap.call(VIDIOC_REQBUFS,&req);
    if(req.count<2) throw std::runtime_error("Insufficient V4L2 buffers");
    for(unsigned i=0;i<req.count;++i) {
      v4l2_buffer b{}; b.type=req.type; b.memory=req.memory; b.index=i;
      cap.call(VIDIOC_QUERYBUF,&b);
      void *data=mmap(nullptr,b.length,PROT_READ|PROT_WRITE,MAP_SHARED,cap.fd,b.m.offset);
      if(data==MAP_FAILED) throw std::runtime_error("mmap failed");
      cap.buffers.push_back({data,b.length}); cap.call(VIDIOC_QBUF,&b);
    }
    int type=V4L2_BUF_TYPE_VIDEO_CAPTURE; cap.call(VIDIOC_STREAMON,&type); cap.streaming=true;
    RCLCPP_INFO(node->get_logger(),"%s: %dx%d YUYV @ %d FPS -> rgb8 /wrist_camera/image_raw",
      device.c_str(),width,height,fps);
    uint64_t count=0,gaps=0,bad=0; uint32_t previous_sequence=0;
    int64_t previous_stamp=0,window=clock_ns(CLOCK_MONOTONIC); uint64_t window_count=0;
    while(rclcpp::ok()) {
      pollfd p{cap.fd,POLLIN,0}; int rc=poll(&p,1,2000);
      if(rc<0 && errno==EINTR) continue;
      if(!rclcpp::ok()) break;
      if(rc<=0 || (p.revents&(POLLERR|POLLHUP|POLLNVAL)))
        throw std::runtime_error("Camera disconnected or no frame for 2 seconds");
      v4l2_buffer b{}; b.type=req.type; b.memory=req.memory;
      if(ioctl(cap.fd,VIDIOC_DQBUF,&b)<0) {
        if(errno==EAGAIN || errno==EINTR) continue;
        throw std::runtime_error(std::string("Capture failed: ")+strerror(errno));
      }
      if(b.index>=cap.buffers.size()) throw std::runtime_error("Invalid buffer index");
      if(count && b.sequence>previous_sequence+1) gaps+=b.sequence-previous_sequence-1;
      previous_sequence=b.sequence;
      if((b.flags&V4L2_BUF_FLAG_ERROR) || b.bytesused<size_t(fmt.fmt.pix.bytesperline)*height ||
         b.bytesused>cap.buffers[b.index].size) {
        ++bad; cap.call(VIDIOC_QBUF,&b); continue;
      }
      // Reject unknown clock domains rather than label receive time as capture time.
      if((b.flags&V4L2_BUF_FLAG_TIMESTAMP_MASK)!=V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC)
        throw std::runtime_error("Driver timestamp is not CLOCK_MONOTONIC; cannot safely map capture time");
      int64_t mono=clock_ns(CLOCK_MONOTONIC);
      int64_t realtime=clock_ns(CLOCK_REALTIME);
      int64_t stamp=int64_t(b.timestamp.tv_sec)*1000000000LL+b.timestamp.tv_usec*1000LL+realtime-mono;
      if(stamp<=previous_stamp) throw std::runtime_error("Capture timestamp did not increase; check system clock");
      previous_stamp=stamp;
      if(!count) RCLCPP_INFO(node->get_logger(),"Timestamp: V4L2 monotonic -> system time; source=%s",
        (b.flags&V4L2_BUF_FLAG_TSTAMP_SRC_MASK)==V4L2_BUF_FLAG_TSTAMP_SRC_SOE ? "start-of-exposure" : "end-of-frame");
      sensor_msgs::msg::Image msg;
      msg.header.stamp=rclcpp::Time(stamp,RCL_SYSTEM_TIME);
      msg.header.frame_id=frame_id; msg.height=height; msg.width=width;
      msg.encoding="rgb8"; msg.is_bigendian=0; msg.step=width*3; msg.data.resize(size_t(width)*height*3);
      // ITU-R BT.601 limited-range YUYV -> RGB8 (the negotiated UVC mode).
      const auto *base=static_cast<const uint8_t*>(cap.buffers[b.index].data);
      for(int y=0;y<height;++y) {
        const auto *src=base+size_t(y)*fmt.fmt.pix.bytesperline;
        auto *dst=msg.data.data()+size_t(y)*msg.step;
        for(int x=0;x<width;x+=2,src+=4,dst+=6) {
          int u=int(src[1])-128,v=int(src[3])-128;
          for(int j=0;j<2;++j) {
            int c=std::max(0,int(src[j*2])-16);
            dst[j*3]=clamp((298*c+409*v+128)>>8);
            dst[j*3+1]=clamp((298*c-100*u-208*v+128)>>8);
            dst[j*3+2]=clamp((298*c+516*u+128)>>8);
          }
        }
      }
      cap.call(VIDIOC_QBUF,&b);
      if(!rclcpp::ok()) break;
      publisher->publish(std::move(msg)); ++count; ++window_count;
      rclcpp::spin_some(node);
      int64_t now=clock_ns(CLOCK_MONOTONIC);
      if(now-window>=5000000000LL) {
        RCLCPP_INFO(node->get_logger(),"published=%lu rate=%.2f Hz driver_sequence_gaps=%lu bad_frames=%lu",
          count,window_count*1e9/(now-window),gaps,bad);
        window=now; window_count=0;
      }
    }
  } catch(const std::exception &e) {
    RCLCPP_ERROR(node->get_logger(),"%s",e.what()); result=1;
  }
  if(rclcpp::ok()) rclcpp::shutdown();
  return result;
}
