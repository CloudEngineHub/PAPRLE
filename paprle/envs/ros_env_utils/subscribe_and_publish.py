from std_msgs.msg import Header
from sensor_msgs.msg import JointState
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
from functools import partial
from threading import Thread, Lock
import numpy as np
import rospy

class JointStateSubscriber:
    def __init__(self, sub_info, output_joint_names):

        self.output_joint_names = output_joint_names
        self.joint_mapping = {}
        self.sub_info = sub_info
        self.flag_first_state_updated = {}
        for sub_topic, sub_info in sub_info.items():
            sub_msg_type = sub_info['type']
            self.joint_mapping[sub_topic] = None
            subscriber = rospy.Subscriber(
                sub_topic,
                JointState,
                partial(self.listener_callback, topic=sub_topic),
                queue_size=10
            )
            self.flag_first_state_updated[sub_topic] = False
        self.lock = Lock()
        self.states = {
            'pos': np.zeros(len(output_joint_names)),
            'vel': np.zeros(len(output_joint_names)),
            'eff': np.zeros(len(output_joint_names))
        }

    def listener_callback(self, msg, topic):
        if self.joint_mapping[topic] is None:
            self.joint_mapping[topic] = []
            for id, name in enumerate(msg.name):
                if name in self.output_joint_names:
                    self.joint_mapping[topic].append((id, self.output_joint_names.index(name)))
            self.joint_mapping[topic] = np.array(self.joint_mapping[topic])
        with self.lock:
            id1, id2 = self.joint_mapping[topic][:, 0], self.joint_mapping[topic][:, 1]
            self.states['pos'][id2] = np.array(msg.position)[id1]
            self.states['vel'][id2] = np.array(msg.velocity)[id1]
            self.states['eff'][id2] = np.array(msg.effort)[id1]
        self.flag_first_state_updated[topic] = True

class ControllerPublisher:
    def __init__(self, pub_info, command_joint_names, joint_states, timer_period=0.02):

        self.timer_period = timer_period
        self.pub_info = pub_info
        self.pubs = {}
        self.command_pos, self.command_vel, self.command_acc = None, None, None
        self.command_joint_names = command_joint_names
        self.joint_mapping = {}
        self.duration_msg = rospy.Duration(secs=timer_period)
        self.mode = 'direct_publish' # 'interpolate'
        self.interpolate_time_ = {}
        self.interpolate_duration = 3.0
        self.joint_states = joint_states
        self.timers = []
        for pub_topic, pub_info in pub_info.items():
            pub_msg_type = eval(pub_info['type'])
            self.joint_mapping[pub_topic] = [command_joint_names.index(name) for name in pub_info['joint_names']]
            self.pubs[pub_topic] = rospy.Publisher(pub_topic, pub_msg_type, queue_size=10)
            self.interpolate_time_[pub_topic] = 0.0
            # Create a timer to publish messages at the specified period
            timer = rospy.Timer(rospy.Duration(timer_period), partial(self.publish, pub_topic))
            self.timers.append(timer)
        # for timer in self.timers:
        #     timer.start()

    def publish(self, topic, *args, **kwargs):
        if self.command_pos is not None:
            msg = JointTrajectory()
            msg.header = Header()
            msg.header.stamp = rospy.Time.now()
            msg.joint_names = self.pub_info[topic]['joint_names']
            mapping_inds = self.joint_mapping[topic]
            if self.mode == 'direct_publish':
                pos = self.command_pos[mapping_inds]
                vel = [] if self.command_vel is None else self.command_vel[mapping_inds]
                acc = [] if self.command_acc is None else self.command_acc[mapping_inds]
            else:
                ratio = min(self.interpolate_time_[topic]/self.interpolate_duration, 1.0)
                pos = (1-ratio) * self.joint_states['pos'][mapping_inds] + ratio * self.command_pos[mapping_inds]
                vel = [0.0] * len(mapping_inds)
                acc = [0.0] * len(mapping_inds)
                self.interpolate_time_[topic] += self.timer_period
            msg.points = [JointTrajectoryPoint(positions=pos, velocities=vel, accelerations=acc,
                                               time_from_start=self.duration_msg)]
            self.pubs[topic].publish(msg)