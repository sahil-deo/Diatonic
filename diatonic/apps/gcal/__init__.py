from .app import GCalApp


def create(ctx):
    return GCalApp(ctx)
