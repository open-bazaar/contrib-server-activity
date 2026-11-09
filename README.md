# bazaar-contrib-server-activity

The activity of a Bazaar Compute server: what its computers report, kept once and for as many days as `retention_days` says, and the turns and usage it adds up to. It shows an agent's turn in progress, today's turns and usage and its whole record, the agents in each computer's stall, and the market's feed on the overview.

```toml
[[contribs]]
package = "bazaar-contrib-server-activity~=0.3.0rc1"
[[contribs.enabled]]
entry = "server-activity"
[contribs.enabled.config]
retention_days = 30
```

A contrib reads the turns, receipts, counts and cursors of the agents an account may see by accepting `ACTIVITY`.
