/*
 * lzbank - pack files so the ZX Spectrum Next can unpack them across 8K banks
 * em00k / NextBuild
 *
 *   lzbank file                         -> file.lzb
 *   lzbank file -o out.lzb
 *   lzbank -d out.lzb -o check.bin      unpack, to check
 *   lzbank -b a.nxi b.nxi -o set.lzb    a bundle, for LZDepackBankAt
 *   -x  search harder (slower, slightly smaller)   -q  quiet
 *
 * The C port of Scripts/tools/lzbank.py, which documents the format. Same
 * parse, same tie-breaking: the two write the same bytes for the same input
 * (Sources/LZBank/tests/test_lzbank.py checks it).
 *
 * The stream is ZX0 v2 (Einar Saukas) with one rule added: no match crosses
 * an 8K page of the output. Unpack it with LZDepackBank / LZDepackBankAt
 * from Scripts/nextlib_lzbank.bas.
 *
 * Build: cc -O2 -o lzbank lzbank.c       (any C99 compiler; build.sh / build.bat)
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define PAGE        8192
#define MAX_OFFSET  32640           /* ZX0's own limit: offset MSB 255 */
#define END_MSB     256
#define MAX_LIT     65535           /* the Z80 decoder counts in BC */
#define TREE_LEN    256             /* the match tree compares this far */
#define RING        16384           /* > the furthest a token reaches (8192) */

enum { T_LIT = 0, T_REP = 1, T_NEW = 2 };
enum { K_LIT = 0, K_MATCH = 1, K_START = 2 };

static int EB[65537];               /* Elias gamma length in bits */
static int quiet;

static void die(const char *msg)
{
    fprintf(stderr, "lzbank: %s\n", msg);
    exit(1);
}

static void *xalloc(size_t n)
{
    void *p = calloc(1, n ? n : 1);
    if (!p)
        die("out of memory");
    return p;
}

static void *xgrow(void *p, size_t n)
{
    p = realloc(p, n);
    if (!p)
        die("out of memory");
    return p;
}

/* ------------------------------------------------------------------------
 *  Bit writer and reader (ZX0 v2, forwards)
 * ---------------------------------------------------------------------- */

typedef struct {
    uint8_t *p;
    size_t n, cap, at;
    int mask, backtrack;
} Writer;

static void w_byte(Writer *w, int v)
{
    if (w->n == w->cap) {
        w->cap = w->cap ? w->cap * 2 : 4096;
        w->p = xgrow(w->p, w->cap);
    }
    w->p[w->n++] = (uint8_t)v;
}

static void w_bit(Writer *w, int v)
{
    if (w->backtrack) {             /* the first bit of a new offset's length */
        if (v)                      /* rides in bit 0 of its LSB byte */
            w->p[w->n - 1] |= 1;
        w->backtrack = 0;
        return;
    }
    if (!w->mask) {
        w->mask = 128;
        w->at = w->n;
        w_byte(w, 0);
    }
    if (v)
        w->p[w->at] |= (uint8_t)w->mask;
    w->mask >>= 1;
}

static void w_elias(Writer *w, unsigned value, int invert)
{
    unsigned i = 1;
    while (i <= value / 2)
        i <<= 1;                    /* the top bit of value */
    while (i > 1) {
        i >>= 1;
        w_bit(w, 0);
        w_bit(w, invert ? !(value & i) : !!(value & i));
    }
    w_bit(w, 1);
}

typedef struct { int type, a, b; } Token;      /* L: start, n   R: n   N: offset, n */

static uint8_t *encode(const uint8_t *data, const Token *t, size_t nt, size_t *outn)
{
    Writer w = { 0 };
    int first = 1;
    for (size_t k = 0; k < nt; k++) {
        if (t[k].type == 'L') {
            if (!first)
                w_bit(&w, 0);
            w_elias(&w, (unsigned)t[k].b, 0);
            for (int j = 0; j < t[k].b; j++)
                w_byte(&w, data[t[k].a + j]);
        } else if (t[k].type == 'R') {
            w_bit(&w, 0);
            w_elias(&w, (unsigned)t[k].a, 0);
        } else {
            int o = t[k].a, n = t[k].b;
            w_bit(&w, 1);
            w_elias(&w, (unsigned)((o - 1) / 128 + 1), 1);
            w_byte(&w, (127 - (o - 1) % 128) << 1);
            w.backtrack = 1;
            w_elias(&w, (unsigned)(n - 1), 0);
        }
        first = 0;
    }
    w_bit(&w, 1);
    w_elias(&w, END_MSB, 1);
    *outn = w.n;
    return w.p;
}

typedef struct {
    const uint8_t *p;
    size_t n, i;
    int mask, val, back, last;
    const char *err;
} Reader;

static int r_byte(Reader *r)
{
    if (r->i >= r->n) {
        r->err = "the stream ends early";
        return 0;
    }
    r->last = r->p[r->i++];
    return r->last;
}

static int r_bit(Reader *r)
{
    if (r->back) {
        r->back = 0;
        return r->last & 1;
    }
    r->mask >>= 1;
    if (!r->mask) {
        r->mask = 128;
        r->val = r_byte(r);
    }
    return (r->val & r->mask) ? 1 : 0;
}

static unsigned r_elias(Reader *r, int inv)
{
    unsigned v = 1;
    while (!r_bit(r) && !r->err) {
        v = v << 1 | (unsigned)(r_bit(r) ^ inv);
        if (v > 0x1000000) {
            r->err = "a length is out of range";
            break;
        }
    }
    return v;
}

/* ZX0 v2 decoder; checks the bank rule too. Returns NULL with *err set on a bad stream. */
static uint8_t *decode(const uint8_t *p, size_t n, size_t *outn, size_t *used, char *err, size_t errsz)
{
    Reader r = { p, n, 0, 0, 0, 0, 0, NULL };
    size_t cap = 65536, len = 0;
    uint8_t *out = xalloc(cap);
    unsigned last = 1;
    int state = 0;
    for (;;) {
        unsigned off = last, cnt;
        if (state == 0) {
            cnt = r_elias(&r, 0);
            if (r.err)
                break;
            if (cnt > MAX_LIT) {
                snprintf(err, errsz, "literals at %zu: %u of them (limit %d)", len, cnt, MAX_LIT);
                free(out);
                return NULL;
            }
            if (r.i + cnt > n) {
                r.err = "the stream ends early";
                break;
            }
            while (len + cnt > cap)
                out = xgrow(out, cap *= 2);
            memcpy(out + len, p + r.i, cnt);
            r.i += cnt;
            len += cnt;
            state = r_bit(&r) ? 2 : 1;
            continue;
        }
        if (state == 1) {
            cnt = r_elias(&r, 0);
        } else {
            unsigned msb = r_elias(&r, 1);
            if (r.err)
                break;
            if (msb == END_MSB) {
                *outn = len;
                *used = r.i;
                return out;
            }
            off = last = msb * 128 - (unsigned)(r_byte(&r) >> 1);
            r.back = 1;
            cnt = r_elias(&r, 0) + 1;
        }
        if (r.err)
            break;
        if (off == 0 || off > len) {
            snprintf(err, errsz, "match at %zu reaches before the start (%u)", len, off);
            free(out);
            return NULL;
        }
        if (cnt > PAGE - len % PAGE) {
            snprintf(err, errsz, "match at %zu length %u crosses a page end", len, cnt);
            free(out);
            return NULL;
        }
        while (len + cnt > cap)
            out = xgrow(out, cap *= 2);
        for (unsigned k = 0; k < cnt; k++, len++)
            out[len] = out[len - off];
        state = r_bit(&r) ? 2 : 0;
    }
    snprintf(err, errsz, "%s", r.err ? r.err : "bad stream");
    free(out);
    return NULL;
}

/* ------------------------------------------------------------------------
 *  The parse
 * ---------------------------------------------------------------------- */

static int match_len(const uint8_t *d, size_t j, size_t i, int lim)
{
    int l = 0;
    while (l < lim && d[j + l] == d[i + l])
        l++;
    return l;
}

typedef struct { int off, len; } Match;

typedef struct {
    const uint8_t *d;
    size_t n;
    int32_t *son;                   /* per position: smaller, larger subtree */
    int32_t head[65536];
    int cut;
} Tree;

/* LZMA's bt2 over the ZX0 window: add position i, return its matches,
 * each longer and further back than the one before. */
static int tree_insert(Tree *t, size_t i, Match *found)
{
    const uint8_t *d = t->d;
    int lim = (int)(t->n - i < TREE_LEN ? t->n - i : TREE_LEN);
    if (lim < 2)
        return 0;
    int h = d[i] | d[i + 1] << 8;
    int32_t cur = t->head[h];
    t->head[h] = (int32_t)i;
    size_t p1 = 2 * i, p0 = 2 * i + 1;
    int len0 = 0, len1 = 0, best = 1, nf = 0, left = t->cut;
    for (;;) {
        if (cur < 0 || i - (size_t)cur > MAX_OFFSET || !left) {
            t->son[p0] = t->son[p1] = -1;
            return nf;
        }
        left--;
        int ln = len0 < len1 ? len0 : len1;
        if (d[cur + ln] == d[i + ln]) {
            ln++;
            if (ln < lim)
                ln += match_len(d, (size_t)cur + ln, i + ln, lim - ln);
            if (ln > best) {
                best = ln;
                found[nf].off = (int)(i - (size_t)cur);
                found[nf].len = ln;
                nf++;
                if (ln >= lim) {
                    t->son[p1] = t->son[2 * (size_t)cur];
                    t->son[p0] = t->son[2 * (size_t)cur + 1];
                    return nf;
                }
            }
        }
        if (d[cur + ln] < d[i + ln]) {
            t->son[p1] = cur;
            p1 = 2 * (size_t)cur + 1;
            cur = t->son[p1];
            len1 = ln;
        } else {
            t->son[p0] = cur;
            p0 = 2 * (size_t)cur;
            cur = t->son[p0];
            len0 = ln;
        }
    }
}

typedef struct {
    int key, cost, ll;
    int32_t par;
    uint32_t tok;                   /* type | len << 2 | offset << 16 */
} Arrival;

typedef struct {
    Arrival *e;
    int n, cap;
} Slot;

static Slot ring[RING];

/* like a Python dict: a new key goes on the end, a cheaper one replaces in place */
static void relax(size_t pos, int key, int cost, int ll, int32_t par, uint32_t tok)
{
    Slot *s = &ring[pos & (RING - 1)];
    for (int k = 0; k < s->n; k++) {
        if (s->e[k].key == key) {
            if (cost < s->e[k].cost) {
                s->e[k].cost = cost;
                s->e[k].ll = ll;
                s->e[k].par = par;
                s->e[k].tok = tok;
            }
            return;
        }
    }
    if (s->n == s->cap) {
        s->cap = s->cap ? s->cap * 2 : 16;
        s->e = xgrow(s->e, (size_t)s->cap * sizeof(Arrival));
    }
    Arrival *a = &s->e[s->n++];
    a->key = key;
    a->cost = cost;
    a->ll = ll;
    a->par = par;
    a->tok = tok;
}

typedef struct { int cut, keep, nice, force; } Params;

static Token *parse(const uint8_t *d, size_t n, Params pr, size_t *ntok, const char *label)
{
    if (n == 0)
        die("nothing to pack");
    if (n > 0x7FFFFFF)
        die("the file is too big");
    const int K = pr.keep;
    int32_t *bt_par = xalloc((n + 1) * K * sizeof(int32_t));
    uint32_t *bt_tok = xalloc((n + 1) * K * sizeof(uint32_t));
    for (size_t k = 0; k < (n + 1) * K; k++)
        bt_par[k] = -1;
    Tree *tree = xalloc(sizeof(Tree));
    tree->d = d;
    tree->n = n;
    tree->cut = pr.cut;
    tree->son = xalloc(2 * n * sizeof(int32_t));
    for (size_t k = 0; k < 2 * n; k++)
        tree->son[k] = -1;
    for (int k = 0; k < 65536; k++)
        tree->head[k] = -1;
    Match *found = xalloc(((size_t)pr.cut + 2) * sizeof(Match));
    Arrival *arr = xalloc((size_t)K * sizeof(Arrival));
    Arrival *tmp = NULL;
    int tmpcap = 0;
    for (int k = 0; k < RING; k++)
        ring[k].n = 0;
    relax(0, K_START << 16 | 1, 0, 0, -1, 0);

    long final = -1;
    size_t skip_to = 0, next_report = 65536;
    clock_t t0 = clock();

    for (size_t i = 0; i <= n; i++) {
        Slot *slot = &ring[i & (RING - 1)];
        int nf = i < n ? tree_insert(tree, i, found) : 0;
        if (slot->n == 0 || i < skip_to) {
            slot->n = 0;
            continue;
        }
        /* the K cheapest, ties in arrival order (a stable sort) */
        if (slot->n > tmpcap) {
            tmpcap = slot->n * 2;
            tmp = xgrow(tmp, (size_t)tmpcap * sizeof(Arrival));
        }
        int na = 0;
        for (int k = 0; k < slot->n; k++) {
            Arrival a = slot->e[k];
            int j = na < K ? na : K;
            if (j == K && a.cost >= tmp[K - 1].cost)
                continue;
            while (j > 0 && tmp[j - 1].cost > a.cost) {
                if (j < K)
                    tmp[j] = tmp[j - 1];
                j--;
            }
            tmp[j] = a;
            if (na < K)
                na++;
        }
        memcpy(arr, tmp, (size_t)na * sizeof(Arrival));
        slot->n = 0;
        size_t base = i * K;
        for (int s = 0; s < na; s++) {
            bt_par[base + s] = arr[s].par;
            bt_tok[base + s] = arr[s].tok;
        }
        if (i == n) {
            final = (long)base;
            break;
        }
        if (!quiet && i >= next_report) {
            next_report += 65536;
            fprintf(stderr, "\r  %s%d%%  %.0fs", label, (int)(i * 100 / n),
                    (double)(clock() - t0) / CLOCKS_PER_SEC);
        }

        int room = PAGE - (int)(i % PAGE);
        int lim = n - i < (size_t)room ? (int)(n - i) : room;   /* a match stays in its page */
        int moff = i < MAX_OFFSET ? (int)i : MAX_OFFSET;

        /* literals: one byte from every arrival */
        for (int s = 0; s < na; s++) {
            int kind = arr[s].key >> 16, off = arr[s].key & 0xFFFF, c, nl;
            if (kind == K_LIT) {
                if (arr[s].ll >= MAX_LIT)
                    continue;
                c = arr[s].cost + 8 + EB[arr[s].ll + 1] - EB[arr[s].ll];
                nl = arr[s].ll + 1;
            } else {
                c = arr[s].cost + (kind == K_MATCH ? 10 : 9);
                nl = 1;
            }
            relax(i + 1, off, c, nl, (int32_t)(base + s), T_LIT);
        }

        int longest = 0;

        /* rep matches: only straight after literals */
        for (int s = 0; s < na; s++) {
            if (arr[s].key >> 16 != K_LIT)
                continue;
            int r = arr[s].key & 0xFFFF;
            if (r > moff || d[i - r] != d[i])
                continue;
            int L = match_len(d, i - r, i, lim);
            int mkey = K_MATCH << 16 | r;
            int top = L <= pr.nice ? L : pr.nice;
            for (int k = 1; k <= top; k++)
                relax(i + k, mkey, arr[s].cost + 1 + EB[k], 0, (int32_t)(base + s), T_REP | (uint32_t)k << 2);
            if (L > pr.nice)
                relax(i + L, mkey, arr[s].cost + 1 + EB[L], 0, (int32_t)(base + s), T_REP | (uint32_t)L << 2);
            if (L > longest)
                longest = L;
        }

        /* new offsets: the tree's matches, cut to the page, then each
         * length from the nearest offset that reaches it */
        if (nf && lim >= 2) {
            if (found[nf - 1].len >= TREE_LEN && lim > TREE_LEN)
                found[nf - 1].len = match_len(d, i - found[nf - 1].off, i, lim);
            int bc = arr[0].cost, k = 2;
            for (int f = 0; f < nf; f++) {
                int o = found[f].off, L = found[f].len;
                if (L > lim)
                    L = lim;
                if (L < k)
                    continue;
                int mc = bc + 8 + EB[(o - 1) / 128 + 1];
                int mkey = K_MATCH << 16 | o;
                uint32_t tk = T_NEW | (uint32_t)o << 16;
                int top = L <= pr.nice ? L : pr.nice;
                for (int kk = k; kk <= top; kk++)
                    relax(i + kk, mkey, mc + EB[kk - 1], 0, (int32_t)base, tk | (uint32_t)kk << 2);
                if (L > pr.nice)
                    relax(i + L, mkey, mc + EB[L - 1], 0, (int32_t)base, tk | (uint32_t)L << 2);
                k = L + 1;
            }
            if (k - 1 > longest)
                longest = k - 1;
        }

        if (longest >= pr.force)
            skip_to = i + longest;  /* take it: nothing inside it is expanded */
    }
    if (!quiet)
        fprintf(stderr, "\r%30s\r", "");
    if (final < 0)
        die("can't pack: over 64K with no repeat in it");

    /* walk back */
    size_t cap = 1024, nt = 0;
    Token *t = xalloc(cap * sizeof(Token));
    for (long s = final; s >= 0;) {
        int32_t par = bt_par[s];
        if (par < 0)
            break;
        uint32_t tok = bt_tok[s];
        int typ = tok & 3, ln = (tok >> 2) & 0x3FFF, off = tok >> 16;
        if (nt == cap)
            t = xgrow(t, (cap *= 2) * sizeof(Token));
        if (typ == T_LIT) {
            t[nt].type = 'l';
            t[nt].a = (int)(s / K) - 1;
        } else if (typ == T_REP) {
            t[nt].type = 'R';
            t[nt].a = ln;
        } else {
            t[nt].type = 'N';
            t[nt].a = off;
            t[nt].b = ln;
        }
        nt++;
        s = par;
    }
    /* reverse, and literal bytes into runs */
    size_t no = 0;
    Token *out = xalloc((nt + 1) * sizeof(Token));
    for (size_t k = nt; k-- > 0;) {
        Token x = t[k];
        if (x.type == 'l') {
            if (no && out[no - 1].type == 'L' && out[no - 1].a + out[no - 1].b == x.a) {
                out[no - 1].b++;
            } else {
                out[no].type = 'L';
                out[no].a = x.a;
                out[no].b = 1;
                no++;
            }
        } else {
            out[no++] = x;
        }
    }
    free(t);
    free(bt_par);
    free(bt_tok);
    free(tree->son);
    free(tree);
    free(found);
    free(arr);
    free(tmp);
    *ntok = no;
    return out;
}

static uint8_t *pack(const uint8_t *d, size_t n, Params pr, size_t *outn, const char *label)
{
    size_t nt, un, used;
    char err[200];
    Token *t = parse(d, n, pr, &nt, label);
    uint8_t *p = encode(d, t, nt, outn);
    free(t);
    uint8_t *back = decode(p, *outn, &un, &used, err, sizeof err);
    if (!back || un != n || used != *outn || memcmp(back, d, n))
        die("internal error: the packed stream does not unpack to the input");
    free(back);
    return p;
}

/* ------------------------------------------------------------------------
 *  Files and the command line
 * ---------------------------------------------------------------------- */

static uint8_t *load(const char *path, size_t *n)
{
    FILE *f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "lzbank: can't open %s\n", path);
        exit(1);
    }
    size_t cap = 65536, len = 0, got;
    uint8_t *p = xalloc(cap);
    while ((got = fread(p + len, 1, cap - len, f)) > 0) {
        len += got;
        if (len == cap)
            p = xgrow(p, cap *= 2);
    }
    fclose(f);
    *n = len;
    return p;
}

static void save(const char *path, const uint8_t *p, size_t n)
{
    FILE *f = fopen(path, "wb");
    if (!f || fwrite(p, 1, n, f) != n || fclose(f)) {
        fprintf(stderr, "lzbank: can't write %s\n", path);
        exit(1);
    }
}

static const char *base_name(const char *path)
{
    const char *b = path;
    for (const char *s = path; *s; s++)
        if (*s == '/' || *s == '\\')
            b = s + 1;
    return b;
}

static char *with_suffix(const char *path, const char *add, int replace)
{
    size_t n = strlen(path);
    char *s = xalloc(n + strlen(add) + 1);
    memcpy(s, path, n + 1);
    if (replace) {
        char *dot = strrchr(s, '.');
        if (dot && dot > s + (base_name(s) - s))
            *dot = 0;
    }
    strcat(s, add);
    return s;
}

static void usage(void)
{
    fprintf(stderr,
            "usage: lzbank [-x] [-q] [-o out] file           pack (default out: file.lzb)\n"
            "       lzbank -d [-o out] file.lzb              unpack, to check\n"
            "       lzbank -b [-x] [-q] -o out file file...  a bundle for LZDepackBankAt\n"
            "  -x  search harder: slower, slightly smaller\n"
            "  -q  quiet: no progress or summary\n"
            "  -h  this help\n");
    exit(2);
}

int main(int argc, char **argv)
{
    const char *output = NULL, *inputs[256];
    int ninputs = 0, decompress = 0, bundle = 0, max = 0;

    for (int v = 1; v < 65537; v++) {
        int b = 0;
        while ((v >> (b + 1)) > 0)
            b++;
        EB[v] = 2 * b + 1;
    }
    for (int a = 1; a < argc; a++) {
        const char *s = argv[a];
        if (!strcmp(s, "-o") || !strcmp(s, "--output")) {
            if (++a >= argc)
                usage();
            output = argv[a];
        } else if (!strcmp(s, "-d") || !strcmp(s, "--decompress")) {
            decompress = 1;
        } else if (!strcmp(s, "-b") || !strcmp(s, "--bundle")) {
            bundle = 1;
        } else if (!strcmp(s, "-x") || !strcmp(s, "--max")) {
            max = 1;
        } else if (!strcmp(s, "-q") || !strcmp(s, "--quiet")) {
            quiet = 1;
        } else if (!strcmp(s, "-h") || !strcmp(s, "--help")) {
            usage();
        } else if (s[0] == '-' && s[1]) {
            fprintf(stderr, "lzbank: unknown option %s\n", s);
            usage();
        } else {
            if (ninputs == 256)
                die("too many files");
            inputs[ninputs++] = s;
        }
    }
    if (!ninputs)
        usage();
    Params pr = max ? (Params){ 1024, 16, 256, 512 } : (Params){ 256, 8, 96, 512 };
    clock_t t0 = clock();

    if (bundle) {
        if (!output)
            die("a bundle needs -o");
        size_t head = 2 + 2 * (size_t)ninputs, at = head, total = 0;
        if (head > PAGE)
            die("too many files: the table would pass the first page");
        uint8_t *streams[256];
        size_t slen[256], flen[256], offs[256];
        for (int k = 0; k < ninputs; k++) {
            uint8_t *d = load(inputs[k], &flen[k]);
            char label[300];
            snprintf(label, sizeof label, "%s ", base_name(inputs[k]));
            streams[k] = pack(d, flen[k], pr, &slen[k], label);
            free(d);
            if (at > 0xFFFF) {
                fprintf(stderr, "lzbank: bundle: stream %d would start at %zu: past LZDepackBankAt's 64K reach\n", k, at);
                return 1;
            }
            offs[k] = at;
            at += slen[k];
            total += flen[k];
        }
        uint8_t *out = xalloc(at);
        out[0] = (uint8_t)ninputs;
        out[1] = (uint8_t)(ninputs >> 8);
        for (int k = 0; k < ninputs; k++) {
            out[2 + 2 * k] = (uint8_t)offs[k];
            out[3 + 2 * k] = (uint8_t)(offs[k] >> 8);
            memcpy(out + offs[k], streams[k], slen[k]);
        }
        save(output, out, at);
        if (!quiet) {
            for (int k = 0; k < ninputs; k++)
                printf("  %d  offset %5zu  %6zu -> %5zu  %s\n", k, offs[k], flen[k], slen[k], base_name(inputs[k]));
            printf("%d files, %zu -> %zu bytes, %zu pages -> %s\n", ninputs, total, at, (at + PAGE - 1) / PAGE, output);
        }
        return 0;
    }
    if (ninputs > 1) {
        fprintf(stderr, "lzbank: several inputs make a bundle: add -b\n");
        return 2;
    }

    size_t n;
    uint8_t *data = load(inputs[0], &n);
    if (decompress) {
        size_t un, used;
        char err[200];
        uint8_t *out = decode(data, n, &un, &used, err, sizeof err);
        if (!out) {
            fprintf(stderr, "%s: not an LZBank stream (%s)\n", base_name(inputs[0]), err);
            return 1;
        }
        char *dst = output ? (char *)output : with_suffix(inputs[0], ".unp", 1);
        save(dst, out, un);
        if (!quiet)
            printf("%s: %zu -> %zu bytes, %s\n", base_name(inputs[0]), n, un, dst);
        return 0;
    }

    size_t pn;
    uint8_t *p = pack(data, n, pr, &pn, "");
    char *dst = output ? (char *)output : with_suffix(inputs[0], ".lzb", 0);
    save(dst, p, pn);
    if (!quiet) {
        size_t pin = (n + PAGE - 1) / PAGE, pout = (pn + PAGE - 1) / PAGE;
        printf("%s: %zu -> %zu bytes (%.1f%%), %zu page%s unpack to %zu, %.1fs -> %s\n",
               base_name(inputs[0]), n, pn, pn * 100.0 / n, pout, pout != 1 ? "s" : "", pin,
               (double)(clock() - t0) / CLOCKS_PER_SEC, dst);
    }
    return 0;
}
