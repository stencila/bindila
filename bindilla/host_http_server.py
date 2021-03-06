import json

import tornado
from tornado.ioloop import IOLoop
from tornado.routing import Rule, RuleRouter, PathMatches
from tornado.httpserver import HTTPServer
from tornado.web import Application, RequestHandler
from tornado.httpclient import HTTPClientError

from .host import HOST


class BaseHandler(RequestHandler):
    """
    A base class for all request handlers.

    Adds necessary headers and handles `OPTIONS` requests
    needed for CORS. Handles errors.
    """

    def set_default_headers(self):
        self.set_header('Server', 'Bindilla / Tornado %s' % tornado.version)
        self.set_header('Content-Type', 'application/json')

        # Use origin of request to avoid browser errors like
        # "The value of the 'Access-Control-Allow-Origin' header in the
        # response must not be the wildcard '*' when the
        # request's credentials mode is 'include'."
        origin = self.request.headers.get('Origin', '')
        self.set_header('Access-Control-Allow-Origin', origin)
        self.set_header('Access-Control-Allow-Credentials', 'true')
        self.set_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.set_header('Access-Control-Allow-Headers', 'Content-Type')
        self.set_header('Access-Control-Max-Age', '86400')

    def head(self, *args, **kwargs): #pylint: disable=unused-argument
        self.set_status(204)
        self.finish()

    def options(self, *args, **kwargs): #pylint: disable=unused-argument
        self.set_status(204)
        self.finish()

    def send(self, value):
        body = json.dumps(value, indent=2)
        self.write(body)

    def write_error(self, status_code, **kwargs):
        if 'exc_info' in kwargs:
            _, value, _ = kwargs['exc_info']
            if isinstance(value, ValueError):
                self.set_status(400)
                self.write(str(value))
                return
        RequestHandler.write_error(self, status_code, **kwargs)


class IndexHandler(BaseHandler):
    """
    Handles requests to the index/home page.

    Just redirect to the Github repo. Don't use a 301, or 302,
    because that can cause load balancer helth checks to fail.
    """

    def get(self, *args, **kwargs): #pylint: disable=unused-argument
        self.set_header('Content-Type', 'text/html')
        return self.write('''
        <!DOCTYPE html>
        <html lang="en">
            <head>
                <meta charset="utf-8">
                <meta http-equiv="refresh" content="0; URL='https://github.com/stencila/bindilla#readme'" />
            </head>
            <body>
                <script>window.location = "https://github.com/stencila/bindilla#readme";</script>
            </body>
        </html
        ''')


class ManifestHandler(BaseHandler):
    """
    Handles requests for Bindilla's `Host` manifest.
    """

    def get(self, environs):
        self.send(HOST.manifest(environs.split(',') if environs else None))


class EnvironHandler(BaseHandler):
    """
    Handles requests to launch and shutdown an environment on Binder.
    """

    async def post(self, environ_id):
        """
        Launch a Binder for the environment.
        """
        self.send(await HOST.launch_environ(environ_id))

    async def delete(self, environ_id): #pylint: disable=unused-argument
        """
        Shutdown a Binder for the environment.

        Currently, this is a no-op, but is provided for API compatability
        (clients may request for an environ to be stopped).
        """
        self.set_status(200)
        self.finish()


class ProxyHandler(BaseHandler):
    """
    Proxies requests through to the container running on Binder.
    """

    async def proxy(self, method, binder_id, token, path, body=None): #pylint: disable=too-many-arguments
        try:
            response = await HOST.proxy_environ(method, binder_id, token, path, body)
        except HTTPClientError as error:
            self.set_status(error.code)
            self.write(str(error))
        else:
            for header, value in response.headers.get_all():
                if header not in ('Content-Length', 'Transfer-Encoding', 'Content-Encoding', 'Connection'):
                    self.add_header(header, value)
            if response.body:
                self.set_header('Content-Length', len(response.body))
                self.write(response.body)
        self.finish()

    async def get(self, binder_id, token, path):
        await self.proxy('GET', binder_id, token, path)

    async def post(self, binder_id, token, path):
        await self.proxy('POST', binder_id, token, path, self.request.body)

    async def put(self, binder_id, token, path):
        await self.proxy('PUT', binder_id, token, path, self.request.body)


def make():
    """
    Make the Tornado `RuleRouter`.
    """

    # API v1 endpoints
    v1_app = Application([
        (r'^/?(?P<environs>.*?)/v1/manifest/?', ManifestHandler),
        (r'^.*?/v1/environs/(?P<environ_id>.+)', EnvironHandler),
        (r'^.*?/v1/proxy/(?P<binder_id>[^@]+)\@(?P<token>[^\/]+)/(?P<path>.+)', ProxyHandler)
    ])

    # API v0 endpoints
    v0_app = Application([
        (r'^/?(?P<environs>.*?)/v0/manifest/?', ManifestHandler),
        (r'^.*?/v0/environ/(?P<environ_id>.+)', EnvironHandler),
        (r'^.*?/v0/proxy/(?P<binder_id>[^@]+)\@(?P<token>[^\/]+)/(?P<path>.+)', ProxyHandler)
    ])

    index_app = Application([
        (r'^/', IndexHandler)
    ])

    return RuleRouter([
        Rule(PathMatches(r'^.*?/v1/.*'), v1_app),
        Rule(PathMatches(r'^.*?/v0/.*'), v0_app),
        Rule(PathMatches(r'^/$'), index_app)
    ])


def run():
    """
    Run the HTTP server.
    """
    router = make()
    server = HTTPServer(router)
    server.listen(8888)
    IOLoop.current().start()
