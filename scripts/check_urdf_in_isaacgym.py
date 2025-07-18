from paprle.utils.config_utils import change_working_directory
change_working_directory()
import argparse
from pathlib import Path
import numpy as np
from isaacgym import gymapi
from yourdfpy.urdf import URDF
import os
# https://github.com/dexsuite/dex-urdf/blob/main/example/render_urdf_isaacgym.py
def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("urdf", type=str, help="Path to the URDF file.")
    parser.add_argument(
        "-s", "--simulate", action="store_true", default=True, help="Whether to physically simulate the urdf."
    )
    parser.add_argument(
        "-f", "--fix-root", action="store_true", default=True, help="Whether to physically simulate the urdf."
    )
    parser.add_argument(
        "--disable-self-collision",
        action="store_true",
        default=True,
        help="Whether to disable the self collision of the urdf.",
    )
    return parser.parse_args()


def generate_joint_limit_trajectory(joint_limits: np.ndarray, loop_steps: int):
    trajectory_via_points = np.stack([joint_limits[:, 0], joint_limits[:, 1], joint_limits[:, 0]], axis=1)
    times = np.linspace(0.0, 1.0, int(loop_steps))
    bins = np.arange(3) / 2.0

    # Compute alphas for each time
    inds = np.digitize(times, bins, right=True)
    inds[inds == 0] = 1
    alphas = (bins[inds] - times) / (bins[inds] - bins[inds - 1])

    # Create the new interpolated trajectory
    trajectory = alphas * trajectory_via_points[:, inds - 1] + (1.0 - alphas) * trajectory_via_points[:, inds]
    return trajectory.T


def visualize_urdf(urdf_file, simulate, disable_self_collision, fix_root):
    # initialize gym
    gym = gymapi.acquire_gym()

    # create a simulator
    sim_params = gymapi.SimParams()
    sim_params.substeps = 2
    sim_params.dt = 1.0 / 120.0
    sim_params.physx.solver_type = 1
    sim_params.physx.num_position_iterations = 4
    sim_params.physx.num_velocity_iterations = 1
    sim_params.physx.use_gpu = False
    sim_params.up_axis = gymapi.UP_AXIS_Z

    sim = gym.create_sim(0, 0, gymapi.SIM_PHYSX, sim_params)

    if sim is None:
        print("*** Failed to create sim")
        quit()

    # create viewer using the default camera properties
    viewer = gym.create_viewer(sim, gymapi.CameraProperties())
    if viewer is None:
        raise ValueError("*** Failed to create viewer")

    # add ground plane
    plane_params = gymapi.PlaneParams()
    plane_params.normal = gymapi.Vec3(0, 0, 1)
    plane_params.distance = 1
    gym.add_ground(sim, plane_params)

    # set up the env grid
    spacing = 2
    env_lower = gymapi.Vec3(-spacing, 0.0, -spacing)
    env_upper = gymapi.Vec3(spacing, 0.0, spacing)

    # add cartpole urdf asset
    asset_path = os.path.join(*urdf_file.split("/")[1:])
    asset_root = 'models/'
    asset_name = "urdf_asset"

    # Load asset with default control type of position for all joints
    asset_options = gymapi.AssetOptions()
    asset_options.fix_base_link = fix_root
    asset_options.convex_decomposition_from_submeshes = True
    asset_options.flip_visual_attachments = False # True for some models
    # asset_options.vhacd_enabled = True
    # asset_options.vhacd_params.resolution = 1000000
    # asset_options.vhacd_params.max_convex_hulls = 60
    # asset_options.vhacd_params.max_num_vertices_per_ch = 64
    asset_options.disable_gravity = True#False
    asset = gym.load_asset(sim, asset_root, asset_path, asset_options)
    num_dof = gym.get_asset_dof_count(asset)
    dof_prop = gym.get_asset_dof_properties(asset)
    joint_limits = np.stack([dof_prop["lower"], dof_prop["upper"]], axis=1)

    # Create actor
    env = gym.create_env(sim, env_lower, env_upper, 2)
    initial_pose = gymapi.Transform()
    initial_pose.r = gymapi.Quat(0,0,0,1)#gymapi.Quat(-0.5,-0.5,0.5,0.5)
    if disable_self_collision:
        actor = gym.create_actor(env, asset, initial_pose, asset_name, 0, 1)
    else:
        actor = gym.create_actor(env, asset, initial_pose, asset_name, 0, 0)

    # Set actor DoF position
    dof_states = gym.get_actor_dof_states(env, actor, gymapi.STATE_ALL)
    init_qpos = np.clip(np.zeros(num_dof), joint_limits[:, 0], joint_limits[:, 1])
    dof_states["pos"] = init_qpos
    props = gym.get_actor_dof_properties(env, actor)
    props["driveMode"] = (gymapi.DOF_MODE_POS,) * num_dof
    props["stiffness"] = (1000,) * num_dof
    props["damping"] = (50,) * num_dof
    gym.set_actor_dof_properties(env, actor, props)

    isaacgym_control_names = joint_names = gym.get_actor_dof_names(env, actor)
    print("Joint Names")
    print(isaacgym_control_names)
    urdf = URDF.load(urdf_file)
    mimic_joints_info = []
    for joint_name in joint_names:
        if urdf.joint_map[joint_name].mimic:
            this_idx = isaacgym_control_names.index(joint_name)
            mimic_idx = isaacgym_control_names.index(urdf.joint_map[joint_name].mimic.joint)
            mimic_info = [this_idx, mimic_idx, urdf.joint_map[joint_name].mimic.multiplier,
                          urdf.joint_map[joint_name].mimic.offset]
            mimic_joints_info.append(mimic_info)
    mimic_joints_info = np.array(mimic_joints_info)


    # Create trajectory
    loop_steps = 1000
    trajectory = np.array([])
    if simulate:
        joint_limits[~dof_prop["hasLimits"]] = np.array([0, np.pi * 2])[None, :]
        trajectory = generate_joint_limit_trajectory(joint_limits, loop_steps=loop_steps)

    cam_pos = gymapi.Vec3(1, 0, 1)
    cam_target = gymapi.Vec3(0, 0, 0)
    gym.viewer_camera_look_at(viewer, None, cam_pos, cam_target)

    # Set joint position and position target
    gym.set_actor_dof_states(env, actor, dof_states, gymapi.STATE_POS)
    for i in range(num_dof):
        gym.set_dof_target_position(env, i, init_qpos[i])

    gym.simulate(sim)

    # Simulate
    step = 0
    while not gym.query_viewer_has_closed(viewer):
        event = gym.poll_viewer_events(viewer)
        if event:
            print("")
        if simulate:
            # step the physics
            qpos = trajectory[step] * 0.0
            if len(mimic_joints_info) > 0:
                qpos[mimic_joints_info[:, 0].astype(np.int32)] = qpos[mimic_joints_info[:, 1].astype(np.int32)] * mimic_joints_info[:, 2] + mimic_joints_info[:, 3]
            for i in range(num_dof):
                gym.set_dof_target_position(env, i, qpos[i])
            gym.simulate(sim)
            step += 1
            step = step % loop_steps
        else:
            curr_qpos = np.array(gym.get_actor_dof_states(env,0,0))['pos']
            if len(mimic_joints_info) > 0:
                curr_qpos[mimic_joints_info[:, 0].astype(np.int32)] = curr_qpos[mimic_joints_info[:, 1].astype(np.int32)] * mimic_joints_info[:, 2] + mimic_joints_info[:, 3]
            for i in range(num_dof):
                gym.set_dof_target_position(env, i, curr_qpos[i])
            gym.simulate(sim)
        gym.fetch_results(sim, True)
        # update the viewer
        gym.step_graphics(sim)
        gym.draw_viewer(viewer, sim, True)
        gym.sync_frame_time(sim)

    print("Done")

    gym.destroy_viewer(viewer)
    gym.destroy_sim(sim)

def main():
    args = parse_args()
    visualize_urdf(args.urdf, args.simulate, args.disable_self_collision, args.fix_root)


if __name__ == "__main__":
    main()