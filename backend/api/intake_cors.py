"""Narrow intake CORS, leaving existing administrative routes unchanged."""
from starlette.middleware.cors import CORSMiddleware


class IntakeCORSMiddleware:
    def __init__(self, app, origin):
        self.app = app
        self.intake = CORSMiddleware(app, allow_origins=[origin],
            allow_methods=['GET', 'POST', 'PUT', 'DELETE'],
            allow_headers=['Accept', 'Content-Type', 'X-Drishti-Intake', 'X-Drishti-Filename'])

    async def __call__(self, scope, receive, send):
        target = self.intake if scope['type'] == 'http' and scope['path'].startswith('/api/intake/') else self.app
        await target(scope, receive, send)
