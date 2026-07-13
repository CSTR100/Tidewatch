"""
agents — Person A's half of the Tidewatch swarm.

Commander (tile + dispatch) → Detector (SAR contacts) → Vessel-intel
(AIS match, class, gaps, zones, You.com). All three enrich the shared
SwarmState from contract.py; run_pipeline.py wires them together.
"""
