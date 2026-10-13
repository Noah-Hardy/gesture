# Gesture 0.4.0 release notes (draft: OSC format section)

## The default OSC format is now `float`

Since 0.4.0 the default OSC output format is `float` (one numeric message per landmark, in OSC bundles). In 0.3.x it was `legacy` (the 0.2.x JSON strings).

**Existing installs are migrated once.** 0.3.x saved `"protocol": "legacy"` into almost every `config.json`, whether or not the user chose it, so changing the default alone would only reach fresh installs. On first launch, 0.4.0 switches a config that has `legacy` saved to `float`, records `osc.protocol_migrated: "0.4.0"` so it never repeats, and logs the change. The app then offers **Keep Legacy (0.2.x) format** once. After that, choosing `legacy` again is respected and is never migrated. Configs set to `json` or `float`, and fresh installs, are not touched. `--osc-protocol` still overrides the config for a single run.

If a receiver was built for 0.2.x (addresses such as `/mp/status`), either update it to the `float` or `json` addresses (see the OSC Address Reference), or switch back under **Settings → Advanced → OSC → Output format** or with `--osc-protocol legacy`.

## Deprecation: `legacy` is removed in 1.0

The `legacy` format is deprecated. It stays available, byte-identical to 0.2.x, throughout the 0.x series (0.4 and later) and is removed in 1.0.

## `json` and the network packet size

The `json` wire format is unchanged from 0.3.x, so existing `json` receivers keep working. As in 0.3.x, `/pose/raw` (about 1.76 KB for a full 33-landmark pose) is the one `json` address larger than a single packet and is IP-fragmented; every other address fits in one 1400-byte bundle. On Wi-Fi or other lossy networks, use `float`.
