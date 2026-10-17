"""Headless batch evaluation of the fall rule on the UR Fall Detection Dataset.

The modules are split so the expensive GPU pass runs once and the cheap rule
replay can run many times:

``dataset``  zip extraction, ground-truth download and parsing
``detect``   YOLO + ByteTrack inference, detection cache I/O (needs a GPU)
``replay``   AnomalyEngine replay over cached detections (pure Python)
``metrics``  confusion matrices, latency, recall ceiling, threshold sweep
``report``   markdown / json / jsonl / failure frames

Only ``detect`` imports torch and ultralytics, and only ``report`` imports
OpenCV, so the scoring path stays importable on a bare Python installation.
"""
