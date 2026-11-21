# Qt agent system isolation checks

These checks require a second active Windows logon and a separate Windows host.
Their status remains **not executed** until those resources and evidence are
available. The automated restricted-token and localhost-SMB cases are useful
controls, but are not substitutes for these checks.

## Second active logon

1. Keep an AUT and its authenticated controller running in the first session.
   Record both process IDs, the randomized pipe name, the DLL hash and the
   account/logon identities. Do not put the session secret in reports.
2. Switch to another actually logged-in account/session. Record its identity
   and session ID (`whoami /user`, `whoami /logonid`, `query user`). Confirm its
   logon SID differs from the AUT's logon SID; do not replace this step with a
   restricted token in the first session.
3. While the authorized owner is alive, have it drop only its transport, leaving
   the listener active. Use a raw local `CreateFileW` probe from the second
   session with read/write access to the named pipe. Require
   `ERROR_ACCESS_DENIED` before any request is sent. A connected handle followed
   by a disconnect is insufficient to prove the logon-SID ACL boundary.
4. Establish a positive control: from the same second session, connect to a
   separate controlled test pipe whose DACL explicitly grants that session
   access. Failure to reach both pipes is not a passing isolation result.
5. Reconnect the original controller and require ping/introspection to work.
   Collect the exact API results and timestamps for both Qt5 and Qt6.

## Separate remote host

1. Use a Windows machine distinct from the AUT host. Record both host identities
   and network addresses; a hostname resolving to localhost is insufficient.
2. Configure authenticated SMB access through the existing test environment.
   From that host, successfully connect to a separate remote-enabled control
   pipe on the AUT machine, using a DACL that grants the test identity access.
   This proves network transport and credentials work before testing rejection.
3. With the agent listener available and its authorized owner alive, probe
   `\\AUT-HOST\pipe\<agent-name>` from the remote machine. Require a policy
   denial. Timeout, DNS/credential failure, connection refusal, missing pipe or
   firewall blocking do not establish that agent policy rejected the client.
4. Reconnect locally as the authorized owner and require ping/introspection.
   Repeat for both Qt variants and retain control/agent API results. The result
   concerns the combined IPC policy; do not infer from a generic access denial
   alone which of the ACL or remote-client flag performed the rejection.

Use accounts and hosts provided by the test environment, and keep credentials
out of the evidence. Results from the normal local component suite cover its
available controls; these two system scenarios require separate evidence.
