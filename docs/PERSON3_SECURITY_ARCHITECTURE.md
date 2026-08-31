# Person-3 Security Architecture

```text
Detector
  ↓
Frozen Finding v1
  ↓
Signed Module Run
  ↓
Producer + Active-Key Verification
  ↓
ACTIVE Assessment Gate
  ↓
Immutable Evidence and Run Membership
  ↓
Trusted Assurance Summary
  ↓
Sealed Assessment Snapshot
  ↓
Transactional Audit Outbox
  ↓
Hash-Chained Audit
  ↓
Signed Audit Checkpoint
```

The SDK is convenience code outside the trust boundary. The backend authenticates every
signed run and atomically stores accepted state plus audit intent.

`VERIFY != ACCEPT`: receipt verification does not consume replay state; acceptance does.
A valid signature proves the approved producer/key signed the exact canonical bytes. It
does not prove detector correctness, safe thresholds or truthful evidence. Likewise,
model digest identity is not behavioral safety.

No findings is not a completed assessment: missing module coverage remains `UNKNOWN`, and
only all four represented modules produce `COMPLETE`. An asset-level `REJECT` does not
imply a global system `REJECT`; frozen disposition policy remains explicit.

The audit hash chain detects internal mutation and broken links but cannot alone detect a
clean suffix deletion. An externally retained Ed25519 checkpoint detects truncation only
through its referenced sequence. It does not protect newer records, establish a trusted
timestamp, or remain trustworthy if its signing key is compromised.

The SIH deployment is air-gapped: localhost HTTP, filesystem keys and SQLite. There is no
cloud service, remote signer, analytics dependency, external database, HSM, blockchain,
RBAC or migration framework. Those are future production concerns, not hidden claims.
