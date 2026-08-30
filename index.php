<?php
/**
 * Laragon front controller for SignalBot.
 *
 * - Keeps URL: /jerzagit_botsignal/botsignal/...
 * - Proxies to Flask on 127.0.0.1:5000 (no browser redirect)
 * - If Flask is down: start controls (+ gateway API under /_gateway)
 */

declare(strict_types=1);

session_start();

$flaskHost = '127.0.0.1';
$flaskPort = 5000;
$projectRoot = __DIR__;
$logDir = $projectRoot . DIRECTORY_SEPARATOR . 'logs';
$dataDir = $projectRoot . DIRECTORY_SEPARATOR . 'data';
$statusFile = $dataDir . DIRECTORY_SEPARATOR . 'gateway_start.json';
$lockFile = $dataDir . DIRECTORY_SEPARATOR . 'gateway_start.lock';
$launcher = $projectRoot . DIRECTORY_SEPARATOR . 'gateway_launch.ps1';

foreach ([$logDir, $dataDir] as $dir) {
    if (!is_dir($dir)) {
        @mkdir($dir, 0775, true);
    }
}

function client_is_local(): bool
{
    $ip = $_SERVER['REMOTE_ADDR'] ?? '';
    return in_array($ip, ['127.0.0.1', '::1'], true);
}

function dashboard_is_up(string $host, int $port, float $timeout = 0.35): bool
{
    $errno = 0;
    $errstr = '';
    $fp = @fsockopen($host, $port, $errno, $errstr, $timeout);
    if ($fp === false) {
        return false;
    }
    fclose($fp);
    return true;
}

function ensure_csrf(): string
{
    if (empty($_SESSION['gateway_csrf'])) {
        $_SESSION['gateway_csrf'] = bin2hex(random_bytes(16));
    }
    return $_SESSION['gateway_csrf'];
}

function read_status(string $statusFile): array
{
    if (!is_file($statusFile)) {
        return ['state' => 'idle', 'message' => 'No start job yet.'];
    }
    $raw = (string) @file_get_contents($statusFile);
    $raw = preg_replace('/^\xEF\xBB\xBF/', '', $raw) ?? $raw;
    $data = json_decode($raw, true);
    return is_array($data) ? $data : ['state' => 'idle', 'message' => 'Invalid status file.'];
}

function write_status(string $statusFile, array $data): void
{
    $data['updated_at'] = date('c');
    file_put_contents(
        $statusFile,
        json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES),
        LOCK_EX
    );
}

function lock_is_active(string $lockFile): bool
{
    if (!is_file($lockFile)) {
        return false;
    }
    $pid = (int) trim((string) @file_get_contents($lockFile));
    if ($pid <= 0) {
        @unlink($lockFile);
        return false;
    }
    $out = [];
    @exec('tasklist /FI "PID eq ' . $pid . '" /NH 2>NUL', $out);
    if (str_contains(implode("\n", $out), (string) $pid)) {
        return true;
    }
    @unlink($lockFile);
    return false;
}

function tail_file(string $path, int $max = 4000): string
{
    if (!is_file($path)) {
        return '';
    }
    $raw = @file_get_contents($path);
    if ($raw === false || $raw === '') {
        return '';
    }
    $raw = preg_replace('/^\xEF\xBB\xBF/', '', $raw) ?? $raw;
    return substr($raw, -$max);
}

function spawn_launcher(string $launcher, string $mode, string $workDir): array
{
    $launcherEsc = str_replace("'", "''", $launcher);
    $workEsc = str_replace("'", "''", $workDir);
    $modeEsc = preg_replace('/[^a-z]/', '', $mode) ?? '';

    $ps = "Start-Process -FilePath 'powershell.exe' -ArgumentList @("
        . "'-NoProfile','-ExecutionPolicy','Bypass','-File','{$launcherEsc}','-Mode','{$modeEsc}'"
        . ") -WorkingDirectory '{$workEsc}' -WindowStyle Minimized -PassThru"
        . " | Select-Object -ExpandProperty Id";

    $launch = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ' . escapeshellarg($ps);
    $output = [];
    $code = 0;
    exec($launch, $output, $code);
    $pid = isset($output[0]) ? (int) trim($output[0]) : 0;

    return ['ok' => $code === 0 && $pid > 0, 'pid' => $pid, 'code' => $code, 'raw' => $output];
}

function json_response(array $payload, int $status = 200): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode($payload, JSON_UNESCAPED_SLASHES);
    exit;
}

function public_base_path(): string
{
    $script = str_replace('\\', '/', $_SERVER['SCRIPT_NAME'] ?? '');
    $dir = rtrim(dirname($script), '/');
    return $dir === '' ? '' : $dir;
}

function relative_flask_path(string $base): string
{
    $uriPath = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
    $uriPath = rawurldecode($uriPath);
    if ($base !== '' && str_starts_with($uriPath, $base)) {
        $rel = substr($uriPath, strlen($base));
    } else {
        $rel = $uriPath;
    }
    if ($rel === false || $rel === '') {
        $rel = '/';
    }
    if ($rel[0] !== '/') {
        $rel = '/' . $rel;
    }
    // Strip accidental /index.php
    if (str_starts_with($rel, '/index.php')) {
        $rel = substr($rel, strlen('/index.php')) ?: '/';
    }
    return $rel;
}

function proxy_to_flask(string $host, int $port, string $flaskPath, string $basePrefix): void
{
    if (!function_exists('curl_init')) {
        http_response_code(500);
        header('Content-Type: text/plain; charset=utf-8');
        echo "PHP curl extension is required for the Laragon proxy.";
        exit;
    }

    $query = $_SERVER['QUERY_STRING'] ?? '';
    $url = "http://{$host}:{$port}{$flaskPath}";
    if ($query !== '') {
        $url .= '?' . $query;
    }

    $method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
    $headers = [
        'X-Forwarded-Prefix: ' . $basePrefix,
        'X-Forwarded-Proto: ' . ((!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https' : 'http'),
        'X-Forwarded-For: ' . ($_SERVER['REMOTE_ADDR'] ?? '127.0.0.1'),
        'X-Forwarded-Host: ' . ($_SERVER['HTTP_HOST'] ?? 'localhost'),
    ];

    // Forward useful request headers (not hop-by-hop)
    $forwardNames = ['Content-Type', 'Accept', 'Accept-Language', 'Cookie', 'X-Requested-With'];
    foreach ($forwardNames as $name) {
        $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
        if ($name === 'Content-Type') {
            $key = 'CONTENT_TYPE';
        }
        if (!empty($_SERVER[$key])) {
            $headers[] = $name . ': ' . $_SERVER[$key];
        }
    }

    $body = null;
    if (in_array($method, ['POST', 'PUT', 'PATCH', 'DELETE'], true)) {
        $body = file_get_contents('php://input');
        if ($body === false) {
            $body = '';
        }
        // PHP may have consumed multipart into $_POST — rebuild if needed
        if ($body === '' && !empty($_POST) && str_contains($_SERVER['CONTENT_TYPE'] ?? '', 'application/x-www-form-urlencoded')) {
            $body = http_build_query($_POST);
            $headers = array_values(array_filter(
                $headers,
                static fn($h) => !str_starts_with(strtolower($h), 'content-type:')
            ));
            $headers[] = 'Content-Type: application/x-www-form-urlencoded';
        }
    }

    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_CUSTOMREQUEST => $method,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HEADER => true,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_TIMEOUT => 120,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_ENCODING => '',
    ]);
    if ($body !== null) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
    }

    $raw = curl_exec($ch);
    if ($raw === false) {
        $err = curl_error($ch);
        curl_close($ch);
        http_response_code(502);
        header('Content-Type: text/plain; charset=utf-8');
        echo "Bad gateway to Flask: {$err}";
        exit;
    }

    $status = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $headerSize = (int) curl_getinfo($ch, CURLINFO_HEADER_SIZE);
    curl_close($ch);

    $rawHeaders = substr($raw, 0, $headerSize);
    $responseBody = substr($raw, $headerSize);

    http_response_code($status > 0 ? $status : 502);

    foreach (explode("\r\n", $rawHeaders) as $line) {
        if ($line === '' || str_starts_with(strtolower($line), 'http/')) {
            continue;
        }
        $lower = strtolower($line);
        // Let PHP/Apache own these
        if (str_starts_with($lower, 'transfer-encoding:')) {
            continue;
        }
        if (str_starts_with($lower, 'connection:')) {
            continue;
        }
        if (str_starts_with($lower, 'content-length:')) {
            continue;
        }
        // Rewrite absolute Location redirects from Flask onto the Laragon base path
        if (str_starts_with($lower, 'location:')) {
            $loc = trim(substr($line, strlen('Location:')));
            if (str_starts_with($loc, 'http://127.0.0.1:5000') || str_starts_with($loc, 'http://localhost:5000')) {
                $parts = parse_url($loc);
                $path = $parts['path'] ?? '/';
                $q = isset($parts['query']) ? ('?' . $parts['query']) : '';
                $loc = $basePrefix . $path . $q;
            } elseif (str_starts_with($loc, '/') && $basePrefix !== '' && !str_starts_with($loc, $basePrefix)) {
                $loc = $basePrefix . $loc;
            }
            header('Location: ' . $loc, false);
            continue;
        }
        header($line, false);
    }

    echo $responseBody;
    exit;
}

// ── Resolve paths ───────────────────────────────────────────────────────────
$base = public_base_path();
$rel = relative_flask_path($base);
$online = dashboard_is_up($flaskHost, $flaskPort);
$isLocal = client_is_local();
$csrf = ensure_csrf();

// ── Gateway API (always PHP) ────────────────────────────────────────────────
if (str_starts_with($rel, '/_gateway')) {
    if (!$isLocal) {
        json_response(['ok' => false, 'error' => 'Localhost only.'], 403);
    }

    $action = $_GET['api'] ?? $_POST['api'] ?? '';
    if ($rel === '/_gateway/status' || $action === 'status') {
        $status = read_status($statusFile);
        $status['dashboard_online'] = dashboard_is_up($flaskHost, $flaskPort);
        $status['starting'] = lock_is_active($lockFile);
        $status['log_tail'] = tail_file($logDir . DIRECTORY_SEPARATOR . 'gateway_start.out.log');
        $status['err_tail'] = tail_file($logDir . DIRECTORY_SEPARATOR . 'gateway_start.err.log', 2000);
        $status['portal_url'] = $base . '/';
        json_response(['ok' => true, 'status' => $status]);
    }

    if ($rel === '/_gateway/start' || $action === 'start') {
        if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
            json_response(['ok' => false, 'error' => 'POST required.'], 405);
        }
        $token = (string) ($_POST['csrf'] ?? '');
        if (!hash_equals($csrf, $token)) {
            json_response(['ok' => false, 'error' => 'Invalid CSRF token. Refresh the page.'], 403);
        }
        $mode = (string) ($_POST['mode'] ?? '');
        if (!in_array($mode, ['clean', 'dashboard'], true)) {
            json_response(['ok' => false, 'error' => 'Unknown mode.'], 400);
        }
        if (dashboard_is_up($flaskHost, $flaskPort) && $mode === 'dashboard') {
            json_response(['ok' => true, 'already_online' => true, 'message' => 'Dashboard already online.']);
        }
        if (lock_is_active($lockFile)) {
            json_response(['ok' => false, 'error' => 'A start job is already running.'], 409);
        }
        if (!is_file($launcher)) {
            json_response(['ok' => false, 'error' => 'gateway_launch.ps1 missing.'], 500);
        }
        write_status($statusFile, [
            'state' => 'starting',
            'mode' => $mode,
            'message' => $mode === 'clean' ? 'Launching start_project.ps1 -Clean…' : 'Launching dashboard only…',
            'started_at' => date('c'),
        ]);
        $spawn = spawn_launcher($launcher, $mode, $projectRoot);
        if (!$spawn['ok']) {
            write_status($statusFile, [
                'state' => 'failed',
                'mode' => $mode,
                'message' => 'Failed to spawn launcher (code ' . $spawn['code'] . ').',
            ]);
            json_response(['ok' => false, 'error' => 'Failed to launch process.', 'detail' => $spawn], 500);
        }
        file_put_contents($lockFile, (string) $spawn['pid']);
        json_response(['ok' => true, 'message' => 'Start job launched (PID ' . $spawn['pid'] . ').', 'pid' => $spawn['pid'], 'mode' => $mode]);
    }

    json_response(['ok' => false, 'error' => 'Unknown gateway action.'], 404);
}

// ── Proxy when Flask is up (stay on Laragon URL) ────────────────────────────
if ($online) {
    proxy_to_flask($flaskHost, $flaskPort, $rel, $base);
}

// ── Offline start page ──────────────────────────────────────────────────────
$status = read_status($statusFile);
$starting = lock_is_active($lockFile);
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SignalBot — Start portal</title>
  <style>
    :root { --bg:#0f172a; --card:#1e293b; --text:#e2e8f0; --muted:#94a3b8; --ok:#34d399; --bad:#f87171; --warn:#fbbf24; --accent:#38bdf8; }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; font-family:"Segoe UI",system-ui,sans-serif; color:var(--text);
      background: radial-gradient(1200px 600px at 10% -10%, #1d4ed8 0%, transparent 50%), var(--bg);
      display:grid; place-items:center; padding:24px; }
    .card { width:min(640px,100%); background:#1e293bcc; border:1px solid #334155; border-radius:16px; padding:28px; box-shadow:0 20px 50px rgba(0,0,0,.35); }
    h1 { margin:0 0 8px; font-size:1.5rem; }
    p { margin:0 0 14px; color:var(--muted); line-height:1.5; }
    .status { display:inline-flex; align-items:center; gap:8px; padding:6px 12px; border-radius:999px; font-size:.85rem; font-weight:600; margin-bottom:18px; }
    .status.bad { background:rgba(248,113,113,.15); color:var(--bad); }
    .status.warn { background:rgba(251,191,36,.15); color:var(--warn); }
    .dot { width:8px; height:8px; border-radius:50%; background:currentColor; }
    .actions { display:flex; flex-wrap:wrap; gap:8px; margin:16px 0 8px; }
    button.btn { border:0; cursor:pointer; padding:10px 16px; border-radius:10px; background:var(--accent); color:#0f172a; font-weight:700; }
    button.btn.secondary { background:transparent; color:var(--text); border:1px solid #475569; }
    button.btn:disabled { opacity:.55; cursor:not-allowed; }
    .hint { font-size:.8rem; color:var(--muted); }
    pre.log { margin:12px 0 0; padding:12px 14px; border-radius:10px; background:#020617; color:#cbd5e1; font-size:.75rem; overflow:auto; max-height:240px; white-space:pre-wrap; }
    .msg { margin-top:10px; font-size:.9rem; color:var(--accent); min-height:1.2em; }
    .msg.err { color:var(--bad); }
    code { color:#7dd3fc; }
  </style>
</head>
<body>
  <main class="card">
    <h1>SignalBot Portal</h1>
    <p>URL stays on <code><?= htmlspecialchars($base . '/', ENT_QUOTES) ?></code> — no redirect to :5000. Start Flask, then the portal loads here.</p>
    <div id="badge" class="status <?= $starting ? 'warn' : 'bad' ?>">
      <span class="dot"></span>
      <span id="badge-text"><?= $starting ? 'Starting…' : 'Dashboard offline' ?></span>
    </div>
    <?php if (!$isLocal): ?>
      <p class="msg err">Start controls are localhost-only.</p>
    <?php else: ?>
      <p class="hint"><strong>Start full stack</strong> needs MT5 + Docker. <strong>Dashboard only</strong> starts the web portal.</p>
      <div class="actions" id="actions">
        <button type="button" class="btn" data-mode="clean">Start full stack</button>
        <button type="button" class="btn secondary" data-mode="dashboard">Start dashboard only</button>
        <button type="button" class="btn secondary" id="btn-refresh">Refresh</button>
      </div>
      <div class="msg" id="msg"><?= htmlspecialchars((string) ($status['message'] ?? ''), ENT_QUOTES) ?></div>
      <pre class="log" id="log">Waiting for start output…</pre>
    <?php endif; ?>
  </main>
  <script>
    const CSRF = <?= json_encode($csrf) ?>;
    const BASE = <?= json_encode($base) ?>;
    const IS_LOCAL = <?= $isLocal ? 'true' : 'false' ?>;
    const badge = document.getElementById('badge');
    const badgeText = document.getElementById('badge-text');
    const msg = document.getElementById('msg');
    const logEl = document.getElementById('log');
    const actions = document.getElementById('actions');

    function setBusy(busy) {
      if (!actions) return;
      actions.querySelectorAll('button[data-mode]').forEach(b => b.disabled = busy);
    }

    async function poll() {
      if (!IS_LOCAL) return;
      const r = await fetch(BASE + '/_gateway/status?api=status', { cache: 'no-store' });
      const data = await r.json();
      const s = data.status || {};
      if (msg) { msg.textContent = s.message || ''; msg.classList.toggle('err', s.state === 'failed'); }
      if (logEl) {
        const parts = [];
        if (s.log_tail) parts.push(s.log_tail);
        if (s.err_tail) parts.push('--- stderr ---\n' + s.err_tail);
        logEl.textContent = parts.join('\n') || 'Waiting for start output…';
        logEl.scrollTop = logEl.scrollHeight;
      }
      if (s.dashboard_online) {
        badge.className = 'status warn';
        badgeText.textContent = 'Online — loading portal…';
        setBusy(true);
        setTimeout(() => { window.location.href = BASE + '/'; }, 600);
        return;
      }
      if (s.starting) {
        badge.className = 'status warn';
        badgeText.textContent = 'Starting…';
        setBusy(true);
      } else if (s.state === 'failed') {
        badge.className = 'status bad';
        badgeText.textContent = 'Start failed';
        setBusy(false);
      } else {
        badge.className = 'status bad';
        badgeText.textContent = 'Dashboard offline';
        setBusy(false);
      }
    }

    async function start(mode) {
      setBusy(true);
      if (msg) { msg.classList.remove('err'); msg.textContent = 'Launching…'; }
      const body = new URLSearchParams({ api: 'start', mode, csrf: CSRF });
      const r = await fetch(BASE + '/_gateway/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body
      });
      const data = await r.json();
      if (!data.ok) {
        if (msg) { msg.textContent = data.error || 'Failed'; msg.classList.add('err'); }
        setBusy(false);
        return;
      }
      if (msg) msg.textContent = data.message || 'Started.';
      poll();
    }

    document.querySelectorAll('button[data-mode]').forEach(btn => {
      btn.addEventListener('click', () => start(btn.dataset.mode));
    });
    document.getElementById('btn-refresh')?.addEventListener('click', () => location.reload());
    if (IS_LOCAL) { poll(); setInterval(poll, 2000); }
  </script>
</body>
</html>
