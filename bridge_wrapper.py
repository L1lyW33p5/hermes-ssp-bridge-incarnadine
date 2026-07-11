import sys, os, traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.stderr = open(os.path.join(ROOT, "bridge_crash.log"), "w", buffering=1)
sys.stderr.write("WRAPPER STARTED\n")
sys.stderr.flush()

try:
    sys.path.insert(0, ROOT)
    import hermes_bridge
    bridge = hermes_bridge.HermesSSPBridge()
    sys.stderr.write("BRIDGE CREATED\n")
    sys.stderr.flush()
    bridge.start()
except BaseException as e:
    sys.stderr.write(f"CRASH: {type(e).__name__}: {e}\n")
    traceback.print_exc(file=sys.stderr)
    sys.stderr.flush()
