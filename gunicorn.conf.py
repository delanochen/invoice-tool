# Record routing and timing without query strings, cookies or request bodies.
accesslog = "-"
access_log_format = '%(m)s %(U)s %(s)s %(L)s'

# Reserve capacity for ordinary pages while a client downloads a large archive.
# Each process has its own request-scoped Flask database connections.
workers = 2
worker_class = "gthread"
threads = 8
