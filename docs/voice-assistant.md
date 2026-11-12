# Voice assistant

A simple voice front end for Life Inbox, at `/assistant`.

Tap the mic and speak (or type). One request is handled at a time:

- **Save** — starts with "remember", "remind me", "note", "add", "save",
  "write down" (plus a few Hindi/Hinglish phrases). Saved as a normal text item.
- **Ask** — anything else is answered from the user's own saved items with
  sources, using the existing Ask My Inbox logic (`build_grounded_answer`).
- The reply is shown on screen and read aloud (Gnani text-to-speech, or the
  browser voice if Gnani is not configured).

Intent routing is keyword based (`app/assistant.py`, `classify_utterance`),
not an LLM call.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/assistant` | The page |
| POST | `/api/assistant/voice` | multipart: `file` (audio) **or** `text`. Returns transcript, intent, reply, speech text, citations |
| POST | `/api/assistant/speak` | `{"text": "..."}` → MP3. 501 if Gnani is not configured (the page then uses the browser voice) |

Both API routes use the normal request owner, so they are scoped to the
signed-in user like every other route. Spoken questions are not stored; only
"save" requests create an item.

## Configuration

```
GNANI_API_KEY=            # speech-to-text and text-to-speech
GNANI_LANGUAGE=hi-IN,en-IN
GNANI_TTS_VOICE=Kaveri    # optional
GNANI_TTS_MODEL=timbre-v2.5
GNANI_TTS_LANGUAGE=auto
GNANI_TTS_SPEED=1.0       # 0.85 - 1.15
```

Recordings are converted with **ffmpeg** (`brew install ffmpeg`) into
25-second WAV pieces, because Gnani's REST speech-to-text accepts clips of at
most 30 seconds and does not accept WebM. Without a Gnani key the assistant
falls back to OpenAI transcription if `OPENAI_API_KEY` is set; typing always
works.

## Known limits

- No follow-up questions yet; every request is independent.
- Replies are spoken up to about 450 characters.
- Numbers and dates are not yet rewritten as spoken words for text-to-speech.
- Audio and reply text are sent to Gnani.
