---
name: orb-cloud
description: >
  Interpret, analyze, troubleshoot, and act on home/office network data from Orb Cloud sensors.
  Use this skill whenever the user mentions Orb, Orb Cloud, Orb Score, network monitoring,
  internet quality, lag, latency, jitter, packet loss, Wi-Fi signal, RSSI, speed tests,
  responsiveness scores, reliability scores, network health, connectivity issues,
  "my internet is slow", "is my network working", bufferbloat, TTFB, DNS resolution,
  or any question about the quality or performance of their internet or Wi-Fi connection
  that could be answered with Orb Cloud data. Also trigger when the user wants to
  run a speed test, stream live metrics, compare devices, see historical trends,
  generate a network report, check uptime, assess fitness for video calls or gaming,
  or configure temporary dataset collection via the Orb Cloud API.
allowed-tools:
  - mcp__plugin_orb-cloud_orb-cloud__list_organizations
  - mcp__plugin_orb-cloud_orb-cloud__list_devices
  - mcp__plugin_orb-cloud_orb-cloud__get_device_telemetry
  - mcp__plugin_orb-cloud_orb-cloud__trigger_speedtest
  - mcp__plugin_orb-cloud_orb-cloud__configure_temp_datasets
  - mcp__plugin_orbnet_orbnet__get_all_datasets
  - mcp__plugin_orbnet_orbnet__get_client_info
  - mcp__plugin_orbnet_orbnet__get_responsiveness
  - mcp__plugin_orbnet_orbnet__get_scores_1m
  - mcp__plugin_orbnet_orbnet__get_speed_results
  - mcp__plugin_orbnet_orbnet__get_web_responsiveness
  - mcp__plugin_orbnet_orbnet__get_wifi_link
---

# Orb Cloud Network Intelligence

This skill enables you to retrieve, interpret, analyze, and act on network observability
data from Orb sensors using two complementary data sources:

- **Orb Cloud API** (`orb-cloud-mcp`): Fleet management, device listing, live telemetry
  snapshots, speed test triggering, and temporary dataset configuration. Works remotely
  for any device in the account.
- **Orb Local API** (`orbnet`): Direct measurement data from a sensor's local datasets
  API — scores, responsiveness, speed results, web responsiveness, and Wi-Fi link metrics
  at 1s/15s/1m granularity. Requires network reachability to the sensor and the Local API
  to be enabled on the device.

Use Orb Cloud tools for fleet-wide views, device discovery, and remote actions. Use orbnet
tools when you need detailed measurement data from a specific, reachable sensor. The two
sources complement each other: use Orb Cloud to identify devices and get a quick telemetry
snapshot, then use orbnet to pull detailed datasets from a reachable sensor.

If orbnet tools are not available (the user hasn't installed the orbnet plugin), fall back
to Orb Cloud tools only — `get_device_telemetry` provides live score snapshots but not
detailed time-series measurements.

## Prerequisites

The user must have the `orb-cloud-mcp` MCP server connected. If MCP calls fail with
auth errors, remind the user to check their API key configuration. API keys are created
in [Orb Cloud → Orchestration → API Keys](https://cloud.orb.net/). API access requires
an Orb Cloud Plus, Business, or Enterprise plan.

For detailed measurement data, the user also needs the `orbnet` plugin installed with the
sensor's host and port configured. The sensor must have
[Local API](https://orb.net/docs/deploy-and-configure/datasets-configuration#local-api)
access enabled.

## Key Concept: Orbs Span Multiple Networks

A user's Orb fleet often spans multiple distinct networks — a home network, an office,
a vacation property, a client site, a datacenter, etc. Different Orbs report from
different ISPs, locations, and network environments. The user asking questions may not
currently be on the same network as any of their Orbs — they might be checking their
home network from their phone at work, or monitoring a remote office from home.

This means you should never assume "the user's network" is singular. Instead:

- **Always identify which network/location/device** the user is asking about. Use
  `isp_name`, `city_name`, `network_name`, and device names to disambiguate.
- **When the user says "my network"**, check how many distinct networks exist in
  their device list (group by `isp_name` + `network_name` + `city_name`). If there's
  only one, proceed. If there are multiple, ask which one — or give a summary across all.
- **When comparing devices**, group by network first. Comparing an Orb on home Wi-Fi
  against one in a datacenter on Ethernet is not an apples-to-apples comparison — frame
  it as a cross-network overview rather than a device-vs-device ranking.
- **For troubleshooting**, clarify whether the user is experiencing the issue themselves
  right now (and if so, on which network) or monitoring a remote location. If they're
  remote, they can't physically reposition a device or check cables — tailor advice to
  what's actionable remotely (e.g., "trigger a speed test", "check if the pattern is
  time-based") vs on-site (e.g., "move closer to the router", "try Ethernet").

## Matching the User's Current Connection to Their Orbs

When a user says something like "my internet feels slow right now" or "how's my connection",
it's valuable to determine whether any of their Orbs are on the same network they're
currently using. This turns a vague complaint into a data-backed diagnosis.

### Available signals

Use whatever combination of these is available to identify the user's current network:

1. **User's approximate location** — if geolocation context is provided by the platform,
   compare it against the `city_name` and `latitude`/`longitude` reported by their Orbs.
   A city-level match narrows candidates; a lat/lon match within ~1km is a strong signal.

2. **Conversation context and memory** — the user may have previously mentioned where they
   live, work, or what ISP they use. If you know the user is in Seattle on Comcast, and
   one of their Orbs reports `city_name: Seattle` + `isp_name: Comcast`, that's likely
   their current network.

3. **Ask the user** — if the above signals are ambiguous or unavailable, a short clarifying
   question is fine: "Are you currently at home, or checking on a different location?"
   or "Which network are you on right now — I see Orbs on [network A] and [network B]."

4. **Device name hints** — device names like "iPhone", "MacBook", or a user's personal
   machine name may indicate which Orb is co-located with the user. If the user says
   "my laptop is lagging" and there's an Orb named after a MacBook, that's likely the one.

5. **Public IP comparison** — if the user can provide their current public IP (e.g. by
   visiting a "what is my IP" site), you can match it directly against the `public_ip`
   field in Orb data. An exact match is definitive — they're on the same network. Note
   that `public_ip` may be masked if `identifiable` is not enabled.

### Matching logic

Once you have signals, compare against the Orb fleet's dimension data:

- **Strong match**: Same `public_ip`, or same `isp_name` + same `city_name` + user
  confirms location. You can confidently say "Your Orb at [location] is monitoring the
  network you're currently on."
- **Likely match**: Same `city_name` + same `isp_name` but no IP confirmation. Present
  with reasonable confidence: "This appears to be the same network you're on."
- **Weak match**: Same city but different ISP, or same ISP but different city. Mention
  it as a possibility but ask for confirmation.
- **No match**: The user's current network has no corresponding Orb. Let them know:
  "None of your Orbs appear to be on the network you're using right now. I can still
  show you data from your monitored networks — which one would you like to check?"

### How to use the match

When a match is found, treat the matched Orb(s) as the default context for the
conversation. Instead of asking "which device?" for every request, you can:

- Lead with data from the matched Orb(s) for "how's my network" style questions.
- Correlate the user's real-time experience ("this video call is choppy") with live
  metrics from the co-located Orb.
- Offer to run a speed test on that specific Orb to validate their experience.
- Still mention other Orbs/networks if relevant ("your home network looks fine — the
  issue might be on the other end of the call").

When no match is found, shift to an overview or ask which network to focus on.

## MCP Tools Overview

### Orb Cloud tools (`orb-cloud-mcp`)

| Tool | Description |
|---|---|
| `list_organizations` | List and query organizations in the account hierarchy |
| `list_devices` | List Orb devices with hardware info, location, firmware, and configuration |
| `get_device_telemetry` | Get live connectivity status and Orb Score snapshot for a device |
| `trigger_speedtest` | Trigger on-demand content or top speed tests on a device |
| `configure_temp_datasets` | Configure temporary dataset collection with custom webhook endpoints |

### Orb Local API tools (`orbnet`)

These tools query a sensor's local datasets API directly. They require the sensor to be
network-reachable and have its Local API enabled. Each tool supports stateful polling — the
first call returns all buffered data, and subsequent calls return only new records.

| Tool | Description |
|---|---|
| `get_scores_1m` | 1-minute Orb Scores with sub-scores and summary metrics |
| `get_responsiveness` | Lag, latency, jitter, packet loss at 1s/15s/1m granularity |
| `get_speed_results` | Download/upload speed test results |
| `get_web_responsiveness` | TTFB and DNS resolution times |
| `get_wifi_link` | Wi-Fi signal strength, SNR, link rates, channel info at 1s/15s/1m |
| `get_all_datasets` | Fetch all datasets above in a single call |
| `get_client_info` | Orb API client configuration details |

## How to Respond to Common Requests

### "How's my network?" / General health check

1. List devices via `list_devices` (filter by org if user has provided one).
2. Attempt to match the user's current connection to their Orb fleet (see "Matching the
   User's Current Connection" above). If a match is found, lead with that network's data.
3. If no match or multiple networks, group devices by network (using `isp_name`,
   `network_name`, `city_name`) and either ask which one or give a per-network summary.
4. Get current scores:
   - If the matched device is reachable via orbnet, use `get_scores_1m` for detailed data.
   - Otherwise, use `get_device_telemetry` for a live score snapshot.
5. Present the **Orb Score** and its three components using the interpretation guide below.
6. If scores are low and orbnet is available, drill into `get_responsiveness` or
   `get_speed_results` to explain *why*.

### "My internet is slow" / Troubleshooting

First, establish context: attempt to match the user's current connection to their Orb
fleet (see "Matching the User's Current Connection" above). If a match is found, you
know which Orb(s) to pull data from and that the user is on-site (can take physical
actions like repositioning or restarting). If no match, ask whether they're experiencing
the issue now (and on which network) or monitoring remotely — this determines what
actions are feasible.

Then follow this diagnostic ladder — stop as soon as you identify the root cause.
If orbnet tools are available for the target sensor, use them for steps 1–3 to get
detailed measurements. Otherwise, use `get_device_telemetry` for a score-level overview.

1. **Check the Orb Score breakdown** (`get_scores_1m` or `get_device_telemetry`) — which component is dragging the score down?
   - Low **Responsiveness Score** → high lag or latency. Likely congestion or bufferbloat.
   - Low **Reliability Score** → packet loss or extended unresponsive periods. Could be ISP outage, flaky cable, or Wi-Fi interference.
   - Low **Speed Score** → bandwidth below expectations. Could be ISP throttling, congestion, or Wi-Fi bottleneck.
2. **Compare internet vs router metrics** (`get_responsiveness`) — responsiveness data includes both internet-facing
   (`lag_avg_us`, `latency_avg_us`) and router-facing (`router_lag_avg_us`, `router_latency_avg_us`)
   measurements. If router metrics are healthy but internet metrics are degraded, the problem
   is upstream of the router (ISP or internet). If both are degraded, suspect the local
   network or Wi-Fi link.
3. **Check Wi-Fi signal** (`get_wifi_link`) — if the device is on Wi-Fi, look at `rssi_avg` (signal strength)
   and `snr_avg` (signal-to-noise ratio). Interpret using the thresholds in the reference doc.
4. **Check for load interference** — the `network_state` dimension tells you if a speed test
   was running during the measurement window. During active speed tests, lag and latency
   readings are expected to be elevated.
5. **Look at time patterns** — ask the user what time the problem occurs. If orbnet data
   covers the relevant window, look for correlations. Evening congestion, scheduled backups,
   or other household traffic can explain periodic degradation.

### "Run a speed test"

Trigger an on-demand speed test on the specified device via the MCP server. After
triggering, let the user know results will appear in the speed dataset shortly (content
speed tests typically take under a minute). If streaming is available, offer to stream
results in real time.

### "Compare my devices" / Multi-device analysis

1. List all devices in the organization.
2. Group devices by network (ISP + network name + city). Present groups clearly so the
   user understands which devices share a connection and which are on separate networks.
3. Retrieve latest scores for each device.
4. Present a comparison table showing Orb Score, Responsiveness, Reliability, Speed, network
   type (Wi-Fi vs Ethernet), ISP, and location/network name for each device.
5. Within the same network, highlight outliers — e.g., one device scoring much lower than
   its siblings likely has a local issue (Wi-Fi signal, distance from AP, older hardware).
6. Across different networks, frame differences as expected — different ISPs and locations
   will naturally produce different results. Focus on whether each network meets its own
   expectations rather than ranking networks against each other.

### "Show me trends" / Historical analysis

This requires orbnet tools — Orb Cloud's `get_device_telemetry` only returns a live
snapshot, not historical data. If orbnet is available, use its stateful polling to retrieve
buffered time-series data (scores_1m, responsiveness, speed_results). The amount of data
available depends on the sensor's buffer configuration.

If orbnet is not available, let the user know that trend analysis requires the orbnet
plugin with Local API access to the sensor.

When presenting time-series data:

- Convert timestamps from epoch milliseconds to human-readable times in the user's timezone.
- Convert microsecond values to milliseconds for readability (1 ms = 1000 µs).
- Convert kbps to Mbps for speed values (1 Mbps = 1000 kbps).
- Highlight significant changes, degradations, or improvements.
- Note any correlation between events (e.g., speed test running → lag spike).

### "Stream live data" / Polling for updates

Use orbnet's stateful polling to check for new data. The first call to any orbnet tool
returns all buffered data; subsequent calls return only new records since the last poll.
This makes it efficient to poll periodically for updates. Present new data as it arrives,
highlighting any values that cross concerning thresholds.

This requires the orbnet plugin and Local API access to the sensor.

### "Configure data collection" / Temporary datasets

Use the dataset configuration tool to set up temporary data collection with a webhook
endpoint. This is an advanced feature — confirm with the user what dataset type they want,
what endpoint to send data to, and the desired duration.

### Advanced Diagnostics & Insights

The following analyses are available for deeper investigation. Read
`references/diagnostics.md` in this skill directory for the full playbook when the user's
request matches one of these scenarios:

| Scenario | Trigger phrases |
|---|---|
| **Application fitness** | "Can I take this call?", "good enough for gaming?" |
| **Remote work assessment** | "reliable for work?", "call quality during work hours" |
| **Bottleneck identification** | "Why is my speed slow?", "Is it my ISP or Wi-Fi?" |
| **Outage timeline** | "What happened last night?", "Was there an outage?" |
| **Uptime/SLA tracking** | "What's my uptime?", "total downtime this month" |
| **Bufferbloat detection** | "Fast but laggy", "lag during downloads" |
| **Wi-Fi optimization** | "How to improve Wi-Fi?", "which band should I use?" |
| **Peak hour analysis** | "When is my internet worst?", "best time to download" |
| **Before/after comparison** | "Did the new router help?", "compare before and after" |
| **Degradation trends** | "Is my internet getting worse?", "used to be better" |
| **DNS & web performance** | "Websites load slowly", "DNS is slow" |
| **Network report** | "Weekly summary", "how has my network been?" |
| **Multi-site overview** | "Status across all locations", "how are all my sites?" |

Key principle for speed/bottleneck analysis: never frame speed results as a pure measure
of ISP delivery — they are affected by device hardware, Wi-Fi, and distance from the AP.
See the full bottleneck playbook in `references/diagnostics.md`.

---

## Interpreting Orb Scores

The Orb Score is a composite 0–100 rating of overall connectivity health, combining
Responsiveness, Reliability, and Speed sub-scores.

| Score Range | Rating | Color | Meaning |
|---|---|---|---|
| 90–100 | Excellent | Green | Network is performing very well |
| 80–89 | Good | Light green | Solid performance, minor room for improvement |
| 70–79 | Okay | Yellow | Noticeable room for improvement |
| 50–69 | Fair | Orange | Noticeable issues likely affecting experience |
| 0–49 | Poor | Red | Significant problems needing attention |

### Sub-score details

**Responsiveness Score** (0–100): Derived from lag measurements. Lag is the time to get
a usable response from an internet service, measured continuously. A lag value of
5,000,000 µs (5 seconds) is the "unresponsive" ceiling.

**Reliability Score** (0–100): Reflects how consistently the connection stays responsive
over time. Driven by unresponsive periods and packet loss. As of Orb 1.2.0+, reliability
only impacts the overall score during true outages/disruptions.

**Speed Score** (0–100): Based on content download and upload speed test results (not peak
speed tests). Content speed tests run approximately once per hour. As of Orb 1.2.0+,
speed scores persist in the overall Orb Score even if the last speed test was outside the
selected time window.

---

## Interpreting Key Metrics

### Lag & Latency

| Metric | Good | Acceptable | Concerning | Units |
|---|---|---|---|---|
| Lag | < 50 ms | 50–150 ms | > 150 ms | milliseconds |
| Latency (RTT) | < 20 ms | 20–80 ms | > 80 ms | milliseconds |
| Jitter | < 5 ms | 5–30 ms | > 30 ms | milliseconds |
| Packet loss | 0% | < 1% | > 1% | percent |

Remember: raw API values for lag, latency, and jitter are in **microseconds**. Divide by
1000 to get milliseconds.

### Speed

Context matters heavily for speed interpretation. Compare against the user's ISP plan:

- Convert `download_kbps` and `upload_kbps` by dividing by 1000 to get Mbps.
- Content speed (regular automated tests) reflects typical real-world throughput.
- Peak speed (user-initiated) reflects maximum achievable bandwidth.
- `speed_test_engine`: 0 = Orb's built-in engine, 1 = iperf.

### Web Responsiveness

| Metric | Good | Acceptable | Concerning | Units |
|---|---|---|---|---|
| TTFB | < 200 ms | 200–600 ms | > 600 ms | milliseconds |
| DNS resolution | < 50 ms | 50–200 ms | > 200 ms | milliseconds |

These give a direct read on web browsing experience. High TTFB with normal DNS suggests
server-side or routing issues. High DNS with normal TTFB suggests resolver problems.

### Wi-Fi Signal Quality

| Metric | Excellent | Good | Fair | Poor | Units |
|---|---|---|---|---|---|
| RSSI | > -50 | -50 to -65 | -65 to -75 | < -75 | dBm |
| SNR | > 40 | 25–40 | 15–25 | < 15 | dB |

Additional Wi-Fi context to consider:
- **Channel band**: 2.4 GHz has better range but more interference; 5 GHz/6 GHz offer
  higher speeds but shorter range.
- **PHY mode**: 802.11ax (Wi-Fi 6) > 802.11ac (Wi-Fi 5) > 802.11n (Wi-Fi 4). Older
  standards limit throughput regardless of signal quality.
- **Channel width**: Wider channels (80/160 MHz) enable higher throughput but are more
  susceptible to interference.
- **TX/RX rate**: These are the link rates negotiated with the access point. Actual
  throughput is always lower than link rate.

---

## Dimension Values Reference

### `network_type`

| Value | Meaning |
|---|---|
| 0 | Unknown |
| 1 | Wi-Fi |
| 2 | Ethernet |
| 3 | Other |

### `network_state` (speed test load)

| Value | Meaning |
|---|---|
| 0 | Unknown |
| 1 | Idle (no speed test running) |
| 2 | Content upload in progress |
| 3 | Peak upload in progress |
| 4 | Content download in progress |
| 5 | Peak download in progress |
| 6 | Content test (upload + download) |
| 7 | Peak test (upload + download) |

When `network_state` is not 1 (idle), responsiveness and lag measurements will be impacted
by the load. Note this when interpreting data during speed test windows.

---

## Presenting Results

### Unit conversions to always apply

- Timestamps: epoch milliseconds → human-readable datetime in user's timezone
- Lag/latency/jitter: microseconds (µs) → milliseconds (ms), divide by 1000
- TTFB/DNS: microseconds (µs) → milliseconds (ms), divide by 1000
- Speed: kbps → Mbps, divide by 1000
- `speed_age_ms`: milliseconds → human-readable duration ("2 hours ago")

### Formatting guidance

- Use tables for multi-device comparisons and multi-metric snapshots.
- Use natural language summaries before/after data tables — don't just dump raw numbers.
- Color-code or label values using the threshold tables above when describing status.
- When showing time-series data, highlight inflection points and anomalies.
- If an artifact (chart, dashboard) would help, build one — Orb data lends itself to
  time-series visualizations, score gauges, and device comparison cards.

### Privacy awareness

Some fields are masked by default (public_ip, private_ip, bssid, mac_address, device_name,
orb_name) unless the user has enabled `identifiable=true` in their Orb configuration.
If you see masked values, don't call attention to it — just work with what's available.

---

## Dataset Schemas Reference

For the full column-by-column schemas of all five dataset types (scores_1m,
responsiveness, web_responsiveness_results, speed_results, wifi_link), read
`references/datasets.md` in this skill directory.

---

## Troubleshooting the MCP Connection

### Orb Cloud (`orb-cloud-mcp`)

- **401/403 errors**: API key is invalid or lacks the required permissions. Direct user to
  Orb Cloud → Orchestration → API Keys to verify.
- **429 errors**: Rate limit exceeded. Implement backoff — wait and retry.
- **Connection errors**: The `orb-cloud-mcp` server may not be running or configured.
  Confirm it's listed in the user's MCP server configuration.
- **Empty device list**: The API key may not have Organization or Device read permissions,
  or the organization may have no linked Orbs.

### Orb Local API (`orbnet`)

- **Connection refused / timeout**: The sensor is not reachable. Check that the sensor is
  running, the host and port are correct, and the user's machine can reach the sensor's
  network.
- **Empty responses**: The Local API may not be enabled on the device. Direct user to enable
  [Local API](https://orb.net/docs/deploy-and-configure/datasets-configuration#local-api)
  in the sensor's configuration.
- **orbnet tools not available**: The user hasn't installed the orbnet plugin. Measurement
  data requires this plugin — fall back to `get_device_telemetry` for score snapshots only.
