# Web Service Not Responding to HTTP Requests

**Date**: 2026-01-31

## Problem

The pumphouse-web service was running but not responding to HTTP requests. Connections would timeout. The monitor service was working fine.

## Investigation

### TCP Backlog Queue Full

```
tcp    129    128    0.0.0.0:6443    0.0.0.0:*    LISTEN
        ^      ^
    Queued   Max capacity (FULL!)
```

The TCP listen backlog had overflowed - 129 pending connections vs 128 max. New connections were being silently dropped.

### Thread Analysis

```
PID     TID STAT WCHAN                          COMMAND
326662  326662 Ssl  wait_woken                     python
326662  326696 Ssl  futex_wait_queue               python
326662  326697 Ssl  futex_wait_queue               python
326662  326698 Ssl  futex_wait_queue               python
326662 3059399 Ssl  wait_woken                     python
326662 3059402 Ssl  wait_woken                     python
```

### Kernel Stack Trace

```
wait_woken
sk_wait_data
tcp_recvmsg_locked
tcp_recvmsg
inet_recvmsg
sock_recvmsg
sock_read_iter
vfs_read
ksys_read
```

The main thread was **blocked waiting to read from a TCP socket** - likely a slow/hung external request to the tank sensor API or weather service. Since Flask's development server runs single-threaded by default, this blocked the entire server from accepting new connections.

## Root Cause

Flask's built-in Werkzeug development server was running with default single-threaded configuration. A blocking I/O operation (external HTTP request) caused the server to stop processing the accept loop, leading to connection backlog overflow.

## Fix

Added `threaded=True` to `app.run()` in `monitor/web.py`:

```python
app.run(host=args.host, port=args.port, ssl_context=ssl_context, debug=args.debug, threaded=True)
```

This allows Flask to handle each request in a separate thread, preventing one slow request from blocking the entire server.

## Future Considerations

For production use, consider:
- Using a production WSGI server (Gunicorn, uWSGI)
- Adding request timeouts for external API calls
- Health check monitoring that can restart the service if it becomes unresponsive
