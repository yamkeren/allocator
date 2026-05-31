from prometheus_client import Counter, Gauge, Histogram

sessions_total = Counter(
    "allocator_sessions_total",
    "Total sessions by terminal status",
    ["status"],
)

sessions_active = Gauge(
    "allocator_sessions_active",
    "Currently active sessions",
)

allocation_duration = Histogram(
    "allocator_allocation_duration_seconds",
    "Time spent in the full allocation (reserve + bind) path",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
)

allocation_failures = Counter(
    "allocator_allocation_failures_total",
    "Allocation failures by reason",
    ["reason"],
)

bind_duration = Histogram(
    "allocator_bind_duration_seconds",
    "Time per usbip bind call to a node agent",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

rollback_total = Counter(
    "allocator_rollback_total",
    "Rollbacks triggered by reason",
    ["reason"],
)

devices_by_status = Gauge(
    "allocator_devices_total",
    "Device count by status and class",
    ["status", "device_class"],
)

nodes_by_status = Gauge(
    "allocator_nodes_total",
    "Node count by status",
    ["status"],
)
