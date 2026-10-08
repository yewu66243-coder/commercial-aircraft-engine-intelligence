"""Task-local search diagnostics; never retain request credentials or exception text."""
from contextvars import ContextVar
import logging

search_trace = ContextVar('search_trace', default=None)


def record_search(provider, query, status, *, count=0, http_status=None, message=''):
    event = {'provider': provider, 'query': query, 'status': status, 'result_count': count,
             'http_status': http_status, 'message': message}
    trace = search_trace.get()
    if trace is not None:
        trace.append(event)
    logging.getLogger('research').info('Search diagnostic: %s', event)
    return event
