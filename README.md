# subtitle-translator

Translate the English subtitles embedded in an `.mkv` file to Brazilian Portuguese with a model on [OpenRouter](https://openrouter.ai). Bitmap subtitles (PGS from Blu-ray, VobSub from DVD) are converted to text with OCR first. The result is a subtitle file next to the video, which players such as Jellyfin and Plex load as a "Portuguese (Brazil)" track.

Two commands:

- `subtrans` translates one file.
- `subtrans-sweep` walks a media library and translates each file that has no Portuguese subtitle yet. It never asks a question, so a timer or cron job can run it.

The tool does not download subtitles, sync them, or translate into a language other than pt-BR.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- [MKVToolNix](https://mkvtoolnix.download/) (`mkvmerge`, `mkvextract`)
- For PGS: [Tesseract](https://github.com/tesseract-ocr/tesseract) with the English model. `pgsrip` is a Python dependency.
- For VobSub: [vobsub2srt](https://github.com/ruediger/VobSub2SRT), built from source
- An OpenRouter API key in `OPENROUTER_API_KEY`. OpenRouter bills each request, and the subtitle text of each file goes to OpenRouter and the model provider.

## Install

Debian 12+ or Ubuntu 22.04+:

```bash
./install.sh
```

The script installs the system packages, tries to build `vobsub2srt`, installs uv, and runs `uv sync`. You can run it again safely.

macOS:

```bash
brew install mkvtoolnix tesseract uv
uv sync
```

## Translate one file

```bash
export OPENROUTER_API_KEY=...
uv run subtrans movie.mkv                   # auto-pick the English track
uv run subtrans movie.mkv --list            # show the subtitle tracks
uv run subtrans movie.mkv --track 3         # use track 3, in any language
uv run subtrans movie.mkv --limit 20        # sample: the first 20 cues only
uv run subtrans movie.mkv --dry-run         # extract and parse, no API calls
uv run subtrans movie.mkv --model <slug>    # any OpenRouter model slug
```

Run `uv run subtrans --help` for every option and the default model.

The auto-pick uses a full English track and prefers text over bitmap. It ignores "forced" tracks, which hold only the lines of foreign-language dialogue. When no track has a language tag, it reads them as English. It refuses a file that already has a Portuguese track. Use `--track` to override it.

## Output files

For `Movie (2008).mkv`:

| File | When |
| --- | --- |
| `Movie (2008).pt-BR.srt` | Text SRT tracks and all OCR output |
| `Movie (2008).pt-BR.ass` | ASS/SSA tracks, with their styling kept |
| `Movie (2008).sample20.pt-BR.srt` | A `--limit 20` run. The name keeps it apart from a full translation |
| `<output>.progress.json` | While a run is in progress. It is deleted on success |
| `<output>.manual.json` | Cues the model refused to translate |

`--out` accepts `.srt`, `.ass`, `.ssa` and `.vtt`. `subtrans` refuses to overwrite an existing output file unless you pass `--force`.

## Failures and resume

The tool saves a checkpoint after each batch of cues. Run the same command again, and it continues from the checkpoint.

- **Network or provider errors** are retried with backoff. If they continue, the run stops and keeps the checkpoint.
- **A bad API key or an unknown model** stops the run before any work. No credit stops it at the first request.
- **A cue the model refuses or garbles** is isolated by splitting the batch in halves. When one cue still fails, the tool keeps the English text with the prefix `«MANUAL» ` and lists it in `<output>.manual.json` with its timestamps. The run then finishes, and you translate those cues by hand.
- **If the model refuses more than 10% of the cues** (and more than 5), the model is the problem, not the text. The tool writes no subtitle and keeps the checkpoint. Try another `--model`, or delete the checkpoint to retry the refused cues.

`subtrans` exits with 1 on an error.

## Sweep a library

```bash
uv run subtrans-sweep /media/movies /media/series --dry-run   # list the files it would translate
uv run subtrans-sweep /media/movies /media/series
```

The sweep skips a file when:

- a non-forced Portuguese subtitle (`<name>.pt-BR.*`, `.pt.*`, `.por.*` or `.pob.*`, with `.srt`, `.ass` or `.ssa`) sits next to it,
- the file has a Portuguese track, no full English track, or only a track kind this host cannot read,
- the file changed in the last 10 minutes, because an import can still be writing it.

Hidden files and folders are ignored. A file that fails gets no subtitle, so the next sweep tries it again from its checkpoint. A provider outage stops the sweep, because every later file would fail too.

Exit codes: 0 when every file succeeded or was skipped, 1 when at least one file failed, 2 when the run stopped. A stop has one of these causes: a missing or bad key, an unknown model, no credit, an OpenRouter outage, missing MKVToolNix, or a bad command line (for example, a directory that does not exist). Do not start two sweeps on the same library at the same time. A systemd timer does not start a service that is still running.

## Docker

```bash
docker build -t subtrans-sweep .
docker run --rm --init --user "$(id -u):$(id -g)" -e OPENROUTER_API_KEY \
  -v /path/to/library:/library subtrans-sweep /library
```

The library mount must be writable. Set `--user` to the owner of the library, so the subtitles get the same owner. The image has no `vobsub2srt` (Debian does not ship it), so it skips files whose only English track is VobSub.

## Environment

| Variable | Use |
| --- | --- |
| `OPENROUTER_API_KEY` | Required for every run that calls the API |
| `TESSDATA_PREFIX` | Tesseract model directory. Found automatically in the usual Debian and Homebrew paths |

## Limits

- OCR quality depends on Tesseract. Stylized fonts and italics give some errors. The tool restores the `♪` glyph, which Tesseract reads as `#`.
- SRT output drops ASS positioning tags such as `{\an8}`.
- Machine translation is not a human translation. Review important titles.

## Development

```bash
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
```

## License

[MIT](LICENSE)
