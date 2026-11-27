const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');

function identity(directory) {
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const files = path.join(directory, 'identity');
  const keyFile = path.join(files, 'key'), certFile = path.join(files, 'cert');
  const peersFile = path.join(directory, 'remote-peer.json');
  if (!fs.existsSync(files)) {
    const temporary = fs.mkdtempSync(path.join(directory, '.remote-'));
    try {
      execFileSync('/usr/bin/openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650',
        '-subj', '/CN=Sunday Presenter', '-keyout', path.join(temporary, 'key'), '-out', path.join(temporary, 'cert')], { stdio: 'ignore' });
      fs.chmodSync(path.join(temporary, 'key'), 0o600);
      fs.renameSync(temporary, files);
    } finally { fs.rmSync(temporary, { recursive: true, force: true }); }
  }
  const cert = fs.readFileSync(certFile), key = fs.readFileSync(keyFile);
  const fingerprint = new crypto.X509Certificate(cert).fingerprint256.replaceAll(':', '').toLowerCase();
  let saved = fs.existsSync(peersFile) ? JSON.parse(fs.readFileSync(peersFile, 'utf8')) : {};
  if (saved.peerHash && !/^[a-f0-9]{64}$/.test(saved.peerHash)) throw Error('講員配對資料無效，請重設配對。');
  return { tls: { cert, key }, id: fingerprint, peerHash: saved.peerHash,
    remember(peerHash) {
      saved = { peerHash }; const temporary = peersFile + '.tmp';
      fs.writeFileSync(temporary, JSON.stringify(saved), { mode: 0o600 }); fs.renameSync(temporary, peersFile);
    } };
}
module.exports = { identity };
