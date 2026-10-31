import os, time, json, signal, subprocess, tempfile, math
from pathlib import Path
os.environ['ROS_DOMAIN_ID']='83'
duration=float(os.environ.get('Y1_TEST_SECONDS', '30'))
if not 5 <= duration <= 300: raise SystemExit('Invalid test duration')
import rclpy
from rclpy.qos import qos_profile_sensor_data
from y1_msg.msg import ArmJointState, ArmStatus
root=Path.home()/'robot_y1'
if subprocess.run(['pgrep','-x','y1_controller'],capture_output=True).returncode==0:
    raise SystemExit('Existing controller; refusing to run diagnostic')
rclpy.init()
node=rclpy.create_node('y1_disabled_feedback_check')
frames=[]
statuses=[]
def receive(m):
    frames.append({'received':time.monotonic(),'stamp':m.header.stamp.sec+m.header.stamp.nanosec/1e9,
        'joint_position':list(m.joint_position),'joint_velocity':list(m.joint_velocity),'end_pose':list(m.end_pose)})
def status(m):
    statuses.append(list(m.error_code))
node.create_subscription(ArmJointState,'/puppet_arm_right/joint_states',receive,qos_profile_sensor_data)
node.create_subscription(ArmStatus,'/puppet_arm_right/arm_status',status,qos_profile_sensor_data)
command=['ros2','run','y1_controller','y1_controller','--ros-args','-r','__node:=right_normal_arm',
    '--params-file',str(root/'y1_sdk-humble/install/y1_controller/share/y1_controller/config/single_arm_control.yaml'),
    '-p','auto_enable:=false']
(root/'logs').mkdir(exist_ok=True)
with (root/'logs/disabled-feedback-check.log').open('w+') as logfile:
    p=subprocess.Popen(command,stdout=logfile,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        deadline=time.monotonic()+duration+15
        while time.monotonic()<deadline and p.poll() is None:
            rclpy.spin_once(node,timeout_sec=.1)
            if len(frames)>1 and frames[-1]['received']-frames[0]['received']>=duration: break
    finally:
        if p.poll() is None:
            os.killpg(p.pid,signal.SIGINT)
            try:p.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGTERM)
                p.wait(timeout=5)
        logfile.seek(0)
        log_text=logfile.read()
        print('\n'.join(log_text.splitlines()[:20]))
        print('Full log: '+str(root/'logs/disabled-feedback-check.log'))
        node.destroy_node()
        rclpy.shutdown()
valid=bool(frames) and all(len(f['joint_position'])==7 and all(math.isfinite(v) for v in f['joint_position']) for f in frames)
report={'requested_duration_seconds':duration,'auto_enable':False,'motion_commands_sent':False,'frames':len(frames),'valid_positions':valid,
    'latest':frames[-1] if frames else None,'last_motor_status':statuses[-1] if statuses else None,
    'transport_errors':log_text.count('No buffer space available'),'transport_ok':not any(s in log_text for s in ('No buffer space available', 'Failed to write can frame', 'Failed connect to J', 'Failed to open can device')),
    'motors_disabled':bool(statuses) and len(statuses[-1]) == 7 and all(code == 0 for code in statuses[-1]),
    'received_hz':(len(frames)-1)/(frames[-1]['received']-frames[0]['received']) if len(frames)>1 else None}
(root/'deploy/disabled-feedback-validation.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
raise SystemExit(0 if valid and len(frames)>=100 and report['transport_ok'] and report['motors_disabled'] else 1)