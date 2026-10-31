#include <glog/logging.h>

#include <ament_index_cpp/get_package_share_directory.hpp>
#include <csignal>
#include <filesystem>
#include <iostream>
#include <rclcpp/rclcpp.hpp>

#include "y1_controller.h"

using namespace imeta::y1_controller;

int main(int argc, char* argv[]) {
  rclcpp::init(argc, argv);

  std::shared_ptr<Y1Controller> y1_controller;
  int result = 0;
  try {
    y1_controller = std::make_shared<Y1Controller>();
    if (!y1_controller->Init()) {
      std::cerr << "Failed to init Planning module!" << std::endl;
      result = 1;
    } else if (rclcpp::ok()) {
      rclcpp::spin(y1_controller);
    }
  } catch (const std::exception& error) {
    std::cerr << "Y1 driver stopped: " << error.what() << std::endl;
    result = 1;
  }
  y1_controller.reset();  // Run SDK/session cleanup on SIGINT/SIGTERM.
  rclcpp::shutdown();

  return result;
}
