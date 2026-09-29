"""Deterministic transcript cleanup shared by dictation, meetings and evaluation."""
from __future__ import annotations

import difflib
import re

EMOJI = {
    "laugh cry": "😂", "crying laugh": "😂", "tears of joy": "😂", "laughing": "😂",
    "rolling on the floor": "🤣", "sobbing": "😭", "crying": "😢", "heart eyes": "😍",
    "smiling": "😊", "smile": "😊", "thinking": "🤔", "eye roll": "🙄", "shrug": "🤷",
    "skull": "💀", "thumbs up": "👍", "thumbs down": "👎", "clap": "👏", "praying": "🙏",
    "fire": "🔥", "red heart": "❤️", "heart": "❤️", "broken heart": "💔", "hundred": "💯",
    "party": "🎉", "check": "✅", "cross": "❌", "rocket": "🚀", "eyes": "👀",
    "wave": "👋", "muscle": "💪", "coffee": "☕", "pizza": "🍕", "dog": "🐶", "cat": "🐱",
}
SMALL = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(), range(20)))
SMALL.update({"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
              "seventy": 70, "eighty": 80, "ninety": 90})
SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000,
          "billion": 1_000_000_000, "trillion": 1_000_000_000_000}
BIG = {"million", "billion", "trillion"}
SWAP_CLASSES = [set(x.split()) for x in (
    "he she they him her them his hers their i we you me us",
    "monday tuesday wednesday thursday friday saturday sunday",
    "january february march april may june july august september october november december",
    "one two three four five six seven eight nine ten eleven twelve",
    "morning afternoon evening tonight today tomorrow yesterday")]
STARTERS = set("because so and but if when then can could would should i we you he she they it the to let's please send tell".split())
QUESTION = re.compile(r"(?i)^(?:(?:are|can|could|do|does|did|is|will|would|should|shall|have|has|were|was|am)\s+(?:you|we|i|it|there|they|he|she|this|that|anyone|someone)\b|(?:what|when|where|who|why|how)['’](?:s|re|d|ll)\s+\w+|(?:what|when|where|who|why|how)\s+(?:is|are|do|does|did|can|could|will|would|should|have|has|was|were|about|time|many|much|long)\b)")
SIGNAL = re.compile(r"(?i)\b(?:sorry|actually|no[,\s]+wait|wait[,\s]+no|oh[,\s]+no|i mean|scratch that|forget that|never ?mind|correction|let me rephrase)\b")


def words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def word_error_rate(reference: str, hypothesis: str) -> float:
    a, b = words(reference), words(hypothesis)
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, word in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, other in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (word != other))
        prev = cur
    return prev[-1] / len(a)


def _parse_number(tokens: list[str], start: int):
    def clean(i):
        return tokens[i].lower().strip(",.!?")
    first = clean(start)
    try:
        value = float(first.replace(",", ""))
        n = 1
        scale = clean(start + 1) if start + 1 < len(tokens) else ""
        if scale in BIG:
            value *= SCALES[scale]
            n += 1
        return value, "." in first, scale if scale in BIG else None, n
    except ValueError:
        pass
    total = current = 0.0
    i, decimal, factor, scale, seen = start, False, 0.1, None, False
    while i < len(tokens):
        w = clean(i)
        if decimal:
            if w in SMALL and SMALL[w] < 10:
                total += SMALL[w] * factor
                factor /= 10
                i += 1
                continue
            if w in BIG:
                total *= SCALES[w]
                scale = w
                i += 1
            break
        if w in {"a", "an"} and i + 1 < len(tokens) and clean(i + 1) in SCALES:
            current = 1
            seen = True
        elif w in SMALL:
            current += SMALL[w]
            seen = True
        elif w in SCALES and seen:
            if w == "hundred":
                current = (current or 1) * 100
            else:
                total += (current or 1) * SCALES[w]
                current = 0
                scale = w if w in BIG else None
        elif w == "and" and seen and i + 1 < len(tokens) and clean(i + 1) in SMALL | SCALES:
            pass
        elif w == "point" and seen and i + 1 < len(tokens) and clean(i + 1) in SMALL and SMALL[clean(i + 1)] < 10:
            decimal = True
            total += current
            current = 0
        else:
            break
        i += 1
    return (total + current, decimal, scale, i - start) if seen else None


def apply_numbers(text: str) -> str:
    tokens = text.split(" ")
    i = 0
    while i < len(tokens):
        parsed = _parse_number(tokens, i)
        if not parsed:
            i += 1
            continue
        value, decimal, scale, count = parsed
        end = i + count
        nxt = tokens[end].lower().strip(",.!?") if end < len(tokens) else ""
        unit = "$" if nxt in {"dollar", "dollars", "bucks"} else "%" if nxt == "percent" else ""
        if unit:
            end += 1
        if not (value >= 10 or decimal or unit or scale):
            i += 1
            continue
        if not unit and not scale and tokens[i][0].isdigit():
            i += 1
            continue
        if scale in BIG:
            scaled = value / SCALES[scale]
            body = f"{scaled:g} {scale}"
        elif decimal:
            body = f"{value:g}"
        else:
            body = f"{int(value):,}" if value >= 10_000 else str(int(value))
        punct = re.search(r"[,.!?]+$", tokens[end - 1])
        replacement = ("$" + body if unit == "$" else body + unit) + (punct.group() if punct else "")
        tokens[i:end] = [replacement]
        i += 1
    return " ".join(tokens)


def apply_commands(text: str) -> str:
    pattern = re.compile(r"(?i)\b(?:a |an |the )?(" + "|".join(re.escape(x) for x in sorted(EMOJI, key=len, reverse=True)) + r")[\s,-]+emojis?\b")
    text = pattern.sub(lambda m: EMOJI[m.group(1).lower().replace("-", " ").strip()], text)
    text = re.sub(r"([\U0001f300-\U0001faff])[,，](?=\s+\w)", r"\1", text)
    text = apply_numbers(text)
    for pattern, replacement in (
        (r"[,.]?\s*\b(?:new paragraph|paragraph break)\b[,.]?\s*", "\n\n"),
        (r"[,.]?\s*\b(?:new line|newline|line break)\b[,.]?\s*", "\n"),
        (r"[,.]?\s*\b(?:bullet point|dot point|new bullet|next bullet)\b[,.]?\s*", "\n- "),
        (r"\s*\b(?:full stop|fullstop)\b[,.]?", "."),
        (r"\s*\bquestion mark\b[,.]?", "?"),
        (r"\s*\bexclamation (?:mark|point)\b[,.]?", "!"),
    ):
        text = re.sub(pattern, replacement, text, flags=re.I)
    text = re.sub(r"\b([\w.-]+) at ([\w-]+)((?: dot [a-z]{2,4})+)\b", lambda m: m.group(1) + "@" + m.group(2) + m.group(3).replace(" dot ", "."), text, flags=re.I)
    text = re.sub(r"\b([\w-]+)((?: dot [a-z]{2,4})+)\b", lambda m: m.group(1) + m.group(2).replace(" dot ", "."), text, flags=re.I)
    return text.replace(" \n", "\n").strip()


def apply_scratch(text: str) -> str:
    rx = re.compile(r"(?i)\b(?:actually\s+)?(?:scratch that|forget that|never ?mind)\b[,.!?]?\s*")
    while match := rx.search(text):
        before = text[:match.start()].rstrip()
        end = len(before)
        if end and before[-1] in ".!?":
            end -= 1
        previous = max(before.rfind(ch, 0, end) for ch in ".!?")
        text = (before[:previous + 1].rstrip() + " " if previous >= 0 else "") + text[match.end():]
    return text.strip()


def apply_swaps(text: str) -> str:
    rx = re.compile(r"(?i)\b([\w']+)[,.]?\s+(?:actually|no|sorry|i mean)[,.]?\s+([\w']+)\b")
    def swap(m):
        a, b = m.group(1).lower(), m.group(2).lower()
        same = any(a in group and b in group for group in SWAP_CLASSES) or (a.isdigit() and b.isdigit())
        return m.group(2) if a != b and same else m.group()
    return rx.sub(swap, text)


def apply_restarts(text: str) -> str:
    signal = re.compile(r"(?i)[,.]?\s*\b(?:sorry|no[,\s]+wait|wait[,\s]+no|i mean|correction|let me rephrase)\b[,.]?\s*")
    pos = 0
    while m := signal.search(text, pos):
        before = list(re.finditer(r"\S+", text[:m.start()]))
        end = next((i for i in range(len(before) - 1, -1, -1) if before[i].group().endswith((".", "!", "?"))), -1)
        before = before[end + 1:][-12:]
        after = list(re.finditer(r"\S+", text[m.end():]))[:6]
        norm = lambda s: s.lower().strip(",.!?")
        best = (0, -1)
        for k in range(len(before)):
            length = 0
            while k + length < len(before) and length < len(after) and norm(before[k + length].group()) == norm(after[length].group()):
                length += 1
            if length > best[0]:
                best = length, k
        n, k = best
        if n >= 2 or (n == 1 and norm(before[k].group()) in STARTERS):
            start = before[k].start()
            text = text[:start] + text[m.end():]
            pos = start
        else:
            pos = m.end()
    return text


def apply_repeats(text: str) -> str:
    rx = re.compile(r"\S+")
    conjunctions = set("and or but then so because if when".split())
    while True:
        tokens = list(rx.finditer(text))
        changed = False
        for i in range(len(tokens) - 5):
            for gap in range(5):
                j = i + 3 + gap
                if j + 2 >= len(tokens):
                    continue
                norm = lambda k: tokens[k].group().lower().strip(",.!?")
                if [norm(i + x) for x in range(3)] == [norm(j + x) for x in range(3)] and norm(j - 1) not in conjunctions:
                    text = text[:tokens[i].start()] + text[tokens[j].start():]
                    changed = True
                    break
            if changed:
                break
        if not changed:
            return text


def strip_fillers(text: str) -> str:
    text = apply_swaps(apply_repeats(apply_restarts(apply_scratch(text))))
    text = re.sub(r"(?i)\b(\w+),?\s+(a|an|the)\s+\1\b", r"\2 \1", text)
    text = re.sub(r"(?i)(^|[\s,])(?:um+|uh+|uhm|erm?|hmm+|mm+)\b[,.]?\s*", r"\1", text)
    for _ in range(2):
        text = re.sub(r"(?i)\b(\w+)[,.]?\s+\1\b(?=[\s,.!?]|$)", r"\1", text)
    return re.sub(r"^[,\s]+", "", re.sub(r"[ \t]{2,}", " ", text)).strip()


def parse_dictionary(lines: list[str]) -> list[tuple[str, list[str]]]:
    entries = []
    for line in lines:
        match = re.match(r"\s*([^:=()]+?)\s*(?:[:=]\s*(.*)|\((.*)\))?\s*$", line)
        if match:
            term = match.group(1).strip()
            aliases = [x.strip() for x in re.split(r"[,;]", match.group(2) or match.group(3) or "") if x.strip()]
            if term:
                entries.append((term, aliases))
    return entries


def dictionary_terms(lines: list[str]) -> list[str]:
    return [term for term, _ in parse_dictionary(lines)]


def apply_dictionary(text: str, lines: list[str]) -> str:
    for term, aliases in parse_dictionary(lines):
        for alias in sorted([term] + aliases, key=len, reverse=True):
            if len(alias) >= 2:
                pattern = r"\b" + r"[\s-]*".join(map(re.escape, alias.split())) + r"\b"
                text = re.sub(pattern, lambda _: term, text, flags=re.I)
    return text


def merge_dictionary(lines: list[str], learned: list[tuple[str, str]]) -> list[str]:
    lines = lines[:]
    for term, alias in learned:
        for i, (existing, aliases) in enumerate(parse_dictionary(lines)):
            if existing.lower() == term.lower():
                if alias.lower() not in [x.lower() for x in aliases]:
                    lines[i] = existing + ": " + ", ".join(aliases + [alias])
                break
        else:
            lines.append(f"{term}: {alias}")
    return lines


def learn_names(heard: str, corrected: str) -> list[tuple[str, str]]:
    a, b = heard.split(), corrected.split()
    if not a or not b or len(a) >= 400 or len(b) >= 400:
        return []
    common = set("i the a an and but so hi hey hello dear thanks cheers monday tuesday wednesday thursday friday today tomorrow this that we you he she they".split())
    out = []
    for op, ai, aj, bi, bj in difflib.SequenceMatcher(None, [x.lower().strip(".,!?") for x in a], [x.lower().strip(".,!?") for x in b]).get_opcodes():
        if op == "equal" or aj - ai > 4 or bj - bi > 3:
            continue
        fixed = [x.strip(".,!?") for x in b[bi:bj]]
        if fixed and all(x[:1].isupper() and x.lower() not in common for x in fixed):
            term = " ".join(fixed)
            alias = " ".join(a[ai:aj]).lower().strip(".,!?")
            if alias and alias != term.lower() and len(term) >= 3:
                out.append((term, alias))
    return out


def learn_signals(heard: str, corrected: str) -> list[str]:
    a, b = words(heard), words(corrected)
    out = []
    for op, ai, aj, _, _ in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if op in {"delete", "replace"} and aj - ai >= 3 and aj < len(a):
            out.append(" ".join(a[max(ai, aj - 2):aj]))
    return out


def casual(text: str, terms: list[str], supercasual: bool = False) -> str:
    at_start, out = True, []
    for ch in text:
        if at_start and ch.isalpha():
            out.append(ch.lower())
            at_start = False
            continue
        at_start = ch in ".?!" or (at_start and ch.isspace())
        out.append(ch)
    text = re.sub(r"\bI\b", "i", "".join(out))
    text = re.sub(r"[!;:]+", "", text)
    text = re.sub(r"(?i)\b(haha+|lol|lmao)[.,]", r"\1", text)
    if supercasual:
        text = text.replace(",", "").rstrip("., ")
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    for term in terms:
        if len(term) >= 3:
            text = re.sub(re.escape(term), lambda _: term, text, flags=re.I)
    return text


def layout_message(text: str) -> str:
    if "\n" in text:
        return text
    greeting = re.match(r"(?i)^((?:dear|hi|hey|hello|good morning|morning)\s+[\w'-]+[,!]?)[ \t]+", text)
    signoff = re.search(r"(?i)\s+((?:kind regards|best regards|regards|cheers|many thanks|thanks|thank you|best|talk soon)[,.]?)(?:\s+([A-Za-z][\w'-]*))?[.]?\s*$", text)
    if greeting and signoff and signoff.start() > greeting.end() + 2:
        body = text[greeting.end():signoff.start()].strip().rstrip(",")
        if len(body.split()) >= 2:
            return greeting.group(1) + "\n\n" + body + "\n\n" + signoff.group(1) + ("\n" + signoff.group(2) if signoff.group(2) else "")
    return text


def layout_greeting(text: str) -> str:
    if "\n\n" not in text:
        return text
    return re.sub(r"(?i)^((?:hi|hey|hello|dear|good morning)\s+[\w'-]+[,!])\s+(?=\S)", r"\1\n\n", text, count=1)


def apply_lists(text: str) -> str:
    rx = re.compile(r"(?i)\b(?:first|second|third|fourth|fifth|sixth|firstly|secondly|thirdly|fourthly|fifthly|sixthly|number (?:one|two|three|four|five|six))\b[,:.?!;]*\s*")
    matches = list(rx.finditer(text))
    if len(matches) < 2:
        return text
    for number, match in reversed(list(enumerate(matches, 1))):
        text = text[:match.start()] + f"\n{number}. " + text[match.end():]
    text = re.sub(r",?[ \t]*\n(?=\d+\. )", "\n", text)
    return text.strip()


def counted_lists(text: str) -> str:
    if "\n1." in text or "\n- " in text:
        return text
    rx = re.compile(r"(?i)\b(two|three|four|five|six|2|3|4|5|6)\s+(things|reasons|points|steps|options|ways|issues|updates|questions|items|ideas|problems|tips|decisions)\b([^:.\n]{0,80})[:.]\s*")
    match = rx.search(text)
    if not match:
        return text
    n = SMALL.get(match.group(1).lower(), int(match.group(1)) if match.group(1).isdigit() else 0)
    rest = text[match.end():]
    stop = re.search(r"[.!?](?=\s|$)|\n", rest)
    body = rest[:stop.start()] if stop else rest
    tail = rest[stop.end():].strip() if stop else ""
    body = re.sub(r",\s*(?:and|or)\s+", ",", body.replace(";", ","))
    items = [x.strip() for x in body.split(",") if x.strip()]
    if len(items) == n - 1 and items:
        parts = re.split(r"\s+(?:and|or)\s+", items[-1], maxsplit=1)
        if len(parts) == 2:
            items[-1:] = parts
    if len(items) != n:
        return text
    lead = text[:match.end()].strip()
    if lead.endswith("."):
        lead = lead[:-1] + ":"
    return lead + "\n" + "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1)) + ("\n" + tail if tail else "")


def question_marks(text: str) -> str:
    def fix_line(line):
        parts = re.split(r"([.!?])", line)
        out = ""
        for i in range(0, len(parts), 2):
            sentence = parts[i]
            terminator = parts[i + 1] if i + 1 < len(parts) else ""
            body = re.sub(r"^(?:- |\d+[.)] )", "", sentence.strip())
            if QUESTION.match(body) and terminator in {"", "."}:
                terminator = "?"
            out += sentence + terminator
        return out
    return "\n".join(map(fix_line, text.split("\n")))


def strip_echo(output: str, before: str) -> str:
    tail, actual = before.split()[-12:], output.split()
    if len(tail) < 4:
        return output
    norm = lambda x: x.lower().strip(",.!?")
    for n in range(min(len(tail), len(actual)), 3, -1):
        for j in range(min(3, len(actual) - n + 1)):
            if list(map(norm, tail[-n:])) == list(map(norm, actual[j:j + n])):
                return " ".join(actual[j + n:])
    return output


def looks_unfaithful(source: str, output: str, allowed: list[str]) -> bool:
    original, result = words(source), words(output)
    if len(original) < 6:
        return False
    if not 0.35 <= len(result) / len(original) <= 1.6:
        return True
    known = set(original) | {word for term in allowed for word in words(term)}
    novel = sum(len(word) > 3 and word not in known for word in result)
    return novel / max(len(result), 1) > 0.3


def filter_whisper(text: str, segments: list[dict] | None, seconds: float) -> str:
    ghosts = {"thank you", "thanks for watching", "thank you for watching", "bye", "the end", "please subscribe", "like and subscribe", "so", "oh", "um"}
    if segments:
        kept = [str(s.get("text", "")).strip() for s in segments
                if not (s.get("no_speech_prob", 0) > .6 and s.get("avg_logprob", 0) < -.7)
                and s.get("avg_logprob", 0) >= -1.2 and s.get("compression_ratio", 1) <= 2.4
                and str(s.get("text", "")).lower().strip(" .,!") not in ghosts]
        text = " ".join(kept)
    elif text.lower().strip(" .,!") in ghosts:
        text = ""
    text = " ".join(text.split())
    return "" if seconds > 1 and len(text.split()) / seconds > 6 else text


def chinese_script(text: str, traditional: bool) -> str:
    if not traditional or not re.search(r"[\u4e00-\u9fff]", text):
        return text
    try:
        from opencc import OpenCC
        return OpenCC("s2t").convert(text)
    except ImportError:
        return text


def meeting_line(text: str, dictionary: list[str]) -> str:
    text = strip_fillers(text).strip()
    if not any(ch.isalnum() for ch in text):
        return ""
    text = apply_dictionary(apply_numbers(text), dictionary)
    text = re.sub(r"\bi\b", "I", text)
    text = text[:1].upper() + text[1:]
    if text[-1] not in ".?!":
        text += "."
    return question_marks(text)
