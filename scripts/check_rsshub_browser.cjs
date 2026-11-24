/* One visible-browser diagnostic. No stealth, CAPTCHA solver, cookie export or collection writes. */
const { chromium } = require('playwright');

async function main() {
  const target = new URL(process.argv[2] || 'https://rsshub.app/telegram/channel/telegram');
  if (target.protocol !== 'https:' || target.username || target.password || target.hash || target.search || !/^\/telegram\/channel\/[a-zA-Z0-9_]+\/?$/.test(target.pathname)) {
    throw new Error('Нужен HTTPS-адрес одной Telegram-ленты RSSHub, без ключей и параметров.');
  }
  const browser = await chromium.launch({
    headless: false,
    ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : { channel: 'chrome' }),
  });
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    let finish;
    const result = new Promise(resolve => { finish = resolve; });
    const timer = setTimeout(() => finish({ ok: false, reason: 'За 3 минуты XML не получен. Автоматических повторов не было.' }), 180000);
    page.on('close', () => finish({ ok: false, reason: 'Окно закрыто пользователем.' }));
    page.on('response', async response => {
      try {
        if (!response.request().isNavigationRequest() || response.frame() !== page.mainFrame()) return;
        const url = new URL(response.url());
        if (url.origin !== target.origin) return;
        console.log(`Навигация: HTTP ${response.status()} ${url.origin}${url.pathname}`);
        if (response.status() === 403) console.log('Если видна проверка, пройдите её вручную в открытом Chrome. Скрипт её не решает.');
        if (response.status() !== 200 || url.pathname.replace(/\/$/, '') !== target.pathname.replace(/\/$/, '')) return;
        const type = response.headers()['content-type'] || '';
        if (!/(?:xml|rss|atom)/i.test(type)) return;
        if (Number(response.headers()['content-length'] || 0) > 2000000) {
          finish({ ok: false, reason: 'Лента превышает 2 МБ.' }); return;
        }
        const bytes = await response.body();
        const text = bytes.toString('utf8');
        if (bytes.length > 2000000 || !/^\s*(?:<\?xml[^>]*>\s*)?<(?:rss|feed)\b/i.test(text.replace(/^\uFEFF/, ''))) return;
        const posts = (text.match(/<(?:item|entry)(?:\s|>)/g) || []).length;
        finish({ ok: true, status: response.status(), content_type: type, bytes: bytes.length, entries: posts,
          note: 'XML получен обычным видимым браузером; данные и cookies не сохранены.' });
      } catch (error) {
        console.log('Диагностика ответа:', error.name);
      }
    });
    console.log('Открывается одна лента. При необходимости вручную пройдите проверку в Chrome.');
    try { await page.goto(target.href, { waitUntil: 'domcontentloaded', timeout: 30000 }); }
    catch (error) { console.log('Ожидание навигации:', error.name, '— окно остаётся открытым для ручной проверки.'); }
    const outcome = await result;
    clearTimeout(timer);
    console.log(JSON.stringify(outcome, null, 2));
    if (!outcome.ok) process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
