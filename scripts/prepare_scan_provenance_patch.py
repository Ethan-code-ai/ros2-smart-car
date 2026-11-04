"""Generate a reviewed driver variant without overwriting vendor source.

Zero SDK measurements remain unknown: this adapter never invents no_return.
Supports the Slamtec publish_scan/angle-compensation implementation; fails on drift.
"""
import argparse
import hashlib
from pathlib import Path


def instrument(source):
    if 'scan_provenance_pub_' in source:
        raise ValueError('Source already instrumented')
    digest=hashlib.sha256(source.encode()).hexdigest()
    def once(old,new):
        nonlocal source
        if source.count(old)!=1:
            raise ValueError('Driver source differs; review anchor: '+old[:70])
        source=source.replace(old,new,1)
    once('#include <sensor_msgs/msg/laser_scan.hpp>', '#include <sensor_msgs/msg/laser_scan.hpp>\n#include <std_msgs/msg/string.hpp>\n#include <sstream>\n#include <iomanip>\n#include <vector>\n#include <cmath>')
    once('scan_msg->ranges.resize(node_count);','scan_msg->ranges.resize(node_count);')
    once('pub->publish(*scan_msg);', '''// Publish evidence before scan; timestamps and indices are identical.
        if (!scan_provenance_pub_) scan_provenance_pub_ = this->create_publisher<std_msgs::msg::String>("/scan/provenance", 10);
        std::ostringstream evidence;
        const auto stamp = scan_msg->header.stamp;
        evidence << "{\\"stamp\\":[" << stamp.sec << "," << stamp.nanosec
                 << "],\\"frame_id\\":" << std::quoted(frame_id)
                 << ",\\"healthy\\":true,\\"driver_sha256\\":\\"DIGEST\\",\\"kinds\\":[";
        for (size_t j=0; j<node_count; ++j) {
            if(j) evidence << ",";
            const auto raw_index = reverse_data ? node_count-1-j : j;
            const bool unsampled = scan_sampled_.size()==node_count && !scan_sampled_[raw_index];
            const char *kind = std::isfinite(scan_msg->ranges[j]) ? "hit" : (unsampled ? "unsampled" : "unknown");
            evidence << std::quoted(kind);
        }
        evidence << "]}";
        std_msgs::msg::String packet; packet.data=evidence.str();
        scan_provenance_pub_->publish(packet);
        pub->publish(*scan_msg);'''.replace('DIGEST',digest))
    once('int angle_compensate_offset = 0;', 'int angle_compensate_offset = 0;\n                        scan_sampled_.assign(angle_compensate_nodes_count, false);')
    once('if (nodes[i].dist_mm_q2 != 0) {', '''// Track raw angular samples, including zero-distance SDK samples.
                            int raw_bin = static_cast<int>(getAngle(nodes[i])*angle_compensate_multiple);
                            for(size_t k=0; k<angle_compensate_multiple; ++k) {
                                int index=raw_bin+static_cast<int>(k);
                                if(index>=0 && index<angle_compensate_nodes_count) scan_sampled_[index]=true;
                            }
                            if (nodes[i].dist_mm_q2 != 0) {''')
    once('size_t   count = _countof(nodes);','size_t   count = _countof(nodes);\n            scan_sampled_.clear();')
    once('rclcpp::Publisher<sensor_msgs::msg::LaserScan>::SharedPtr scan_pub;',
         'rclcpp::Publisher<sensor_msgs::msg::LaserScan>::SharedPtr scan_pub;\n    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr scan_provenance_pub_;\n    std::vector<bool> scan_sampled_;')
    return source


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.source.resolve()==args.output.resolve() or args.output.exists():
        parser.error('Output must be a new file outside the vendor source')
    args.output.write_text(instrument(args.source.read_text()),encoding='utf-8')
    print('Generated variant. Add std_msgs to package.xml and CMake target dependencies before building.')
