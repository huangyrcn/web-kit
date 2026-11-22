// bioRxiv and medRxiv search: opencli has no adapter for them (2.0.3). Read-only.
// Both run on the same platform (HighWire): a result is a link to /content/<DOI>vN with
// the authors and 'bioRxiv <id>; doi: ...' next to it. The DOI carries the posting date.
import { cli, Strategy } from '@jackwener/opencli/registry';
import { CommandExecutionError, EmptyResultError } from '@jackwener/opencli/errors';

const EXTRACT = `
(() => {
  const norm = v => (v || '').replace(/\\s+/g, ' ').trim();
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/content/10."]')) {
    const m = a.href.match(/\\/content\\/(10\\.\\d+\\/[^?#\\s]+?)(v\\d+)?(?:\\.(?:full|abstract|article)[^/]*)?(?:[?#].*)?$/);
    const title = norm(a.textContent);
    if (!m || !title || title.length < 8 || seen.has(m[1])) continue;
    const item = a.closest('li, .highwire-article-citation, article') || a.parentElement;
    if (!item) continue;
    seen.add(m[1]);
    const authors = norm((item.querySelector('.highwire-citation-authors, .highwire-cite-authors') || {}).textContent);
    const day = m[1].match(/(\\d{4})\\.(\\d{2})\\.(\\d{2})/);
    out.push({
      title,
      authors,
      date: day ? day[1] + '-' + day[2] + '-' + day[3] : '',
      doi: m[1],
      url: a.href.replace(/[?#].*$/, ''),
    });
  }
  return {
    rows: out,
    total: norm((document.querySelector('h1') || {}).textContent),
    title: document.title,
  };
})()
`;

function register(site, host, label) {
  cli({
    site,
    name: 'search',
    access: 'read',
    description: `${label} preprint search`,
    domain: host,
    strategy: Strategy.PUBLIC,
    browser: true,
    args: [
      { name: 'query', positional: true, required: true, help: 'search terms' },
      { name: 'limit', type: 'int', default: 10, help: 'number of results (max 75)' },
    ],
    columns: ['rank', 'title', 'authors', 'date', 'doi', 'url'],
    func: async (page, kwargs) => {
      const query = String(kwargs.query ?? '').trim();
      if (!query) throw new CommandExecutionError('empty query', 'Pass search terms');
      const limit = Math.max(1, Math.min(75, Number.parseInt(String(kwargs.limit ?? 10), 10) || 10));
      const terms = `${query} numresults:${limit} sort:relevance-rank`;
      await page.goto(`https://${host}/search/${encodeURIComponent(terms)}`);
      try {
        await page.wait({ selector: 'a[href*="/content/10."]', timeout: 10 });
      } catch {
        await page.wait(3);
      }
      const data = await page.evaluate(EXTRACT);
      if (!data || !Array.isArray(data.rows)) {
        throw new CommandExecutionError(`${label} search returned an unexpected page`, 'Retry, or check the site in Chrome');
      }
      if (data.rows.length === 0) {
        throw new EmptyResultError(`${site}/search`, `No results, or ${label} showed a check page (${data.title})`);
      }
      return data.rows.slice(0, limit).map((r, i) => ({ rank: i + 1, ...r }));
    },
  });
}

register('biorxiv', 'www.biorxiv.org', 'bioRxiv');
register('medrxiv', 'www.medrxiv.org', 'medRxiv');
