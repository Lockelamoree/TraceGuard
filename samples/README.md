# TraceGuard Sample Evidence

I kept these bundles synthetic so the repo is safe to share and easy to judge.

- `gcp_incident_bundle.txt`: Compact demo bundle covering public Cloud Run access, primitive IAM, suspicious token use, broad ingress, and disabled repo controls.
- `gcp_storage_exfil_bundle.txt`: Higher-signal incident chain covering public storage access, breakglass Owner usage, token generation, service account key creation, broad firewall exposure, credential/exfil alerts, and disabled repo controls.
- `gcp_low_signal_control_bundle.txt`: Low-signal control bundle with least-privilege IAM, internal-only firewall, and enabled repo controls. It should produce no confirmed findings and show that TraceGuard reports inconclusive instead of inventing risk.

To use any bundle, paste the file contents into the Evidence Bundle text area and run `Baseline` or `Run agent`.
