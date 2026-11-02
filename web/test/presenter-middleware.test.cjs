const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

// Run both authorization layers of the actual middleware, without a live
// NextAuth account. In production withAuth can redirect before the body runs.
const source = fs.readFileSync(path.join(__dirname, '../middleware.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const exportsObject = {};
vm.runInNewContext(compiled, { exports: exportsObject, URL, require: name => {
  if (name === '@/app/utils/roles') return { hasEditorAccess: role => ['admin', 'editor', 'beta_tester'].includes(role) };
  if (name === 'next/server') return { NextResponse: {
    next: () => ({ kind: 'next' }), redirect: url => ({ kind: 'redirect', url: String(url) }),
    json: (body, options) => ({ kind: 'json', body, status: options.status })
  } };
  if (name === 'next-auth/middleware') return { withAuth: (handler, options) => req =>
    options.callbacks.authorized({ token: req.nextauth.token, req }) ? handler(req) : { kind: 'sign-in' } };
  throw Error('Unexpected dependency ' + name);
} });
function run(method, path, token = null) {
  return exportsObject.default({ method, nextUrl: new URL('https://dallas-hlc.org' + path),
    url: 'https://dallas-hlc.org' + path, nextauth: { token } });
}
const endpoint = '/api/admin/sunday-services/presenter/upcoming';

test('anonymous desktop can GET precisely the dated folder metadata', () => {
  assert.equal(run('GET', endpoint).kind, 'next');
  assert.equal(run('GET', endpoint + '?cache=off').kind, 'next');
});
test('all writes, adjacent paths and PPT contents retain website authentication', () => {
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'HEAD']) assert.equal(run(method, endpoint).kind, 'sign-in');
  for (const path of ['/admin/sunday-service', '/api/admin/sunday-services', endpoint + '/extra',
    '/api/admin/sunday-services/2026-11-01/ppt/final', '/api/admin/sunday-assistant/state',
    '/api/admin/sunday-services/presenter%2Fupcoming']) assert.equal(run('GET', path).kind, 'sign-in');
});
test('ordinary signed-in users cannot access admin writes; editors retain existing access', () => {
  assert.equal(run('POST', '/api/admin/sunday-services', { role: 'user' }).status, 403);
  assert.equal(run('POST', '/api/admin/sunday-services', { role: 'editor' }).kind, 'next');
});
