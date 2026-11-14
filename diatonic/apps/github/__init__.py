from .app import GitHubApp


def create(ctx):
    return GitHubApp(ctx)
