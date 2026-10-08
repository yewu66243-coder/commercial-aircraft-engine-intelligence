__all__ = ['GPTResearcher']


def __getattr__(name):
    # Indexing utilities do not need the full report-generation dependency tree.
    if name == 'GPTResearcher':
        from .agent import GPTResearcher
        globals()[name] = GPTResearcher
        return GPTResearcher
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
