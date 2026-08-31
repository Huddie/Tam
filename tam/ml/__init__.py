"""tam.ml -- a research harness for building/testing an ML trading signal:
FeatureStore (point-in-time-safe feature registry + materialization/cache),
Model (pluggable architectures, Registry-backed), time_split (leakage-safe
train/val/test), analysis (IC/quantile-spread/hit-rate), and
run_experiment()/run_sweep() tying them together. See docs/ml.md.
"""
