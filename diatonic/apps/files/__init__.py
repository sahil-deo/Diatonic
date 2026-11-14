from .app import FilesApp


def create(ctx):
    return FilesApp(ctx)
