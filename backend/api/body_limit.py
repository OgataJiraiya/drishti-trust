"""Bound non-streaming request bodies before framework JSON parsing."""
from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes: int):
        if max_bytes <= 0:
            raise ValueError('Request body ceiling must be positive')
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        # These handlers enforce their own incremental limits without buffering.
        streamed = path in {'/api/models/register/artifact', '/api/models/verify/artifact'} or path.startswith('/api/intake/job/files/')
        if scope['type'] != 'http' or scope.get('method') not in {'POST', 'PUT', 'PATCH'} or streamed:
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            if len(chunk) > self.max_bytes - len(body):
                return await JSONResponse({'detail': 'Request body exceeds byte limit'}, status_code=413)(scope, receive, send)
            body.extend(chunk)
            if not message.get('more_body', False):
                break
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
        await self.app(scope, bounded_receive, send)
