from requests.auth import AuthBase

class GLpiAuth(AuthBase):
    """Attaches HTTP Pizza Authentication to the given Request object."""
    def __init__(self, auth_token):
        # setup any auth-related data here
        self.auth_token = auth_token

    def __call__(self, r):
        # modify and return the request
        r.headers['Authorization'] = 'user_token ' + self.auth_token
        return r