"""校验固定版本的官方 Linux 包，仅执行 help，不登录或上传记录。"""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).parent
URL = 'https://github.com/YanamiNeko/NekoSportsWorldTool/releases/download/v0.3.0/NekoSportsWorldTool-linux-x64.tar.gz'
SHA256 = '7137fd34ce56b275715c30f243598ab2f8ac332c7e946e84b3d8c181d6a36bf1'


def binary_from_archive(data):
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('archive_digest_mismatch')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        matches = [m for m in archive.getmembers()
                   if Path(m.name).name == 'nekosportsworldtool' and m.isfile()]
        if len(matches) != 1 or not 0 < matches[0].size < 64 * 1024 * 1024:
            raise ValueError('unexpected_archive')
        binary = archive.extractfile(matches[0]).read()
    if binary[:4] != b'\x7fELF' or int.from_bytes(binary[18:20], 'little') != 62:
        raise ValueError('unexpected_binary')
    return binary


def main():
    report = {'stage': 'runtime_check', 'release': 'v0.3.0',
              'platform': platform.machine(), 'libc': list(platform.libc_ver()),
              'account_accessed': False, 'record_uploaded': False,
              'persistent_state_verified': False}
    try:
        if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
            raise ValueError('linux_x64_required')
        request = urllib.request.Request(URL, headers={'User-Agent': 'RuntimeCompatibilityCheck'})
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise ValueError('archive_too_large')
        binary = ROOT / 'nekosportsworldtool'
        binary.write_bytes(binary_from_archive(data))
        binary.chmod(0o700)
        report['sha256_verified'] = True
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, 'HOME': home, 'LANG': 'C.UTF-8'}
            result = subprocess.run([str(binary), 'help'], cwd=home, env=env,
                                    capture_output=True, timeout=30)
        report['help_exit_code'] = result.returncode
        report['runtime_verified'] = result.returncode == 0
    except Exception as exc:
        report['runtime_verified'] = False
        report['failure'] = type(exc).__name__
    (ROOT / 'runtime_report.json').write_text(json.dumps(report), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
