try:
    from paprle.camera.realsense_reader import RealSenseReader
except ImportError as e:
    print("RealSenseReader not available. Ignore if you are not using RealSense cameras.")
    print("Error importing RealSenseReader:", e)
    RealSenseReader = None

CAMERA_DICT = {
    'realsense': RealSenseReader,
}

