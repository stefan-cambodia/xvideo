"""Command line interface: ``xvideo URL [URL...] [options]``."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler

from xvideo import __version__
from xvideo.console import ConsoleReporter, JsonReporter, QuietReporter, print_error
from xvideo.downloader import Downloader, DownloadListener, DownloadOptions, ensure_directory
from xvideo.exceptions import XVideoError
from xvideo.models import DownloadResult, Quality
from xvideo.utils import read_url_file
from xvideo.ytdlp import NetworkOptions, translate_error

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

app = typer.Typer(
    name="xvideo",
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"xvideo {__version__}")
        raise typer.Exit()


def _parse_quality(value: str) -> Quality:
    try:
        return Quality.parse(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command(
    help=(
        "Download the video of public X (Twitter) posts.\n\n"
        "Example: [bold]xvideo https://x.com/USERNAME/status/POST_ID[/bold]\n\n"
        "Only publicly accessible content is supported: private or protected posts, "
        "and content that requires signing in, cannot be downloaded."
    ),
    epilog="Exit status: 0 = success, 1 = at least one download failed, 2 = invalid usage.",
)
def main(
    urls: Annotated[
        list[str] | None,
        typer.Argument(metavar="URL...", help="One or more X post URLs.", show_default=False),
    ] = None,
    file: Annotated[
        Path | None,
        typer.Option(
            "--file",
            "-f",
            metavar="PATH",
            help="Read URLs from a text file, one per line ('#' comments allowed). "
            "Use - for stdin.",
        ),
    ] = None,
    output: Annotated[
        Path,
        typer.Option(
            "--output", "-o", metavar="DIR", help="Output directory (created if it does not exist)."
        ),
    ] = Path(),
    quality: Annotated[
        Quality,
        typer.Option(
            "--quality",
            "-q",
            metavar="QUALITY",
            parser=_parse_quality,
            help="best, worst or a maximum resolution (1080p, 720p…). "
            "Falls back to the best lower quality available.",
        ),
    ] = "best",  # type: ignore[assignment]
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Overwrite existing files instead of adding _1, _2…"),
    ] = False,
    skip_existing: Annotated[
        bool,
        typer.Option("--skip-existing", help="Skip videos whose output file already exists."),
    ] = False,
    metadata: Annotated[
        bool,
        typer.Option(
            "--metadata",
            "-m",
            help="Embed metadata (author, post ID, URL, date, text, resolution, duration). "
            "Requires ffmpeg.",
        ),
    ] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="Print nothing except errors.")] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Debug output, with stack traces on errors.")
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json", help="Print one JSON object per video/error on stdout (JSON Lines)."
        ),
    ] = False,
    timeout: Annotated[
        float,
        typer.Option(
            "--timeout", min=1.0, max=600.0, metavar="SECONDS", help="Network timeout in seconds."
        ),
    ] = 30.0,
    retries: Annotated[
        int,
        typer.Option(
            "--retries",
            min=0,
            max=10,
            metavar="N",
            help="Retries on network errors (with backoff).",
        ),
    ] = 3,
    ffmpeg_location: Annotated[
        Path | None,
        typer.Option(
            "--ffmpeg-location",
            metavar="PATH",
            help="Path to the ffmpeg binary or its directory (default: search PATH).",
        ),
    ] = None,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = None,
) -> None:
    if quiet and verbose:
        raise typer.BadParameter(
            "--quiet and --verbose cannot be used together.", param_hint="'--quiet'"
        )
    if overwrite and skip_existing:
        raise typer.BadParameter(
            "--overwrite and --skip-existing cannot be used together.", param_hint="'--overwrite'"
        )

    all_urls = list(urls or [])
    if file is not None:
        try:
            all_urls += read_url_file(file)
        except OSError as exc:
            raise typer.BadParameter(
                f"cannot read {file}: {exc.strerror or exc}", param_hint="'--file'"
            ) from exc
    if not all_urls:
        raise typer.BadParameter(
            "give at least one URL, or a file with --file.", param_hint="'URL...'"
        )

    console = Console(highlight=False)
    err_console = Console(stderr=True, highlight=False)
    _configure_logging(verbose, err_console if json_output else console)

    listener: DownloadListener
    if json_output:
        listener = JsonReporter()
    elif quiet:
        listener = QuietReporter(err_console, verbose=verbose)
    else:
        listener = ConsoleReporter(console, err_console, verbose=verbose)
        listener.header()

    options = DownloadOptions(
        output_dir=output,
        quality=quality,
        overwrite=overwrite,
        skip_existing=skip_existing,
        embed_metadata=metadata,
        network=NetworkOptions(timeout=timeout, retries=retries),
        ffmpeg_location=ffmpeg_location,
    )
    try:
        # Fail once for the whole batch if the output directory is unusable.
        ensure_directory(output)
        with Downloader(options, listener) as downloader:
            if ffmpeg_location is not None and downloader.ffmpeg_path is None:
                listener.on_warning(f"ffmpeg was not found at {ffmpeg_location}.")
            results = downloader.run(all_urls)
    except KeyboardInterrupt:
        err_console.print(
            "\n[yellow]Interrupted.[/yellow] Partial downloads are kept and "
            "will resume on the next run."
        )
        raise typer.Exit(EXIT_INTERRUPTED) from None
    except XVideoError as error:
        _report_fatal(error, error, all_urls, listener, err_console, verbose=verbose)
        raise typer.Exit(EXIT_FAILURE) from None
    except Exception as exc:  # unexpected bug: still no stack trace unless --verbose
        _report_fatal(translate_error(exc), exc, all_urls, listener, err_console, verbose=verbose)
        raise typer.Exit(EXIT_FAILURE) from None

    if isinstance(listener, ConsoleReporter) and (len(all_urls) > 1 or len(results) > 1):
        listener.summary(results)
    raise typer.Exit(EXIT_OK if all(r.success for r in results) else EXIT_FAILURE)


def _report_fatal(
    error: XVideoError,
    exc: BaseException,
    urls: list[str],
    listener: DownloadListener,
    err_console: Console,
    *,
    verbose: bool,
) -> None:
    """Report an error that aborted the whole batch.

    JSON and quiet modes get one failure per URL; the console gets a single error block.
    """
    if not isinstance(listener, ConsoleReporter):
        for url in urls:
            listener.on_result(DownloadResult.failure(url, error, exception=exc))
        return
    target = urls[0] if len(urls) == 1 else f"{len(urls)} URLs (the whole batch was aborted)"
    print_error(err_console, DownloadResult.failure(target, error, exception=exc), verbose=verbose)


def _configure_logging(verbose: bool, console: Console) -> None:
    logger = logging.getLogger("xvideo")
    logger.handlers.clear()
    logger.propagate = False
    if verbose:
        handler: logging.Handler = RichHandler(
            console=console, show_time=False, show_path=False, markup=False
        )
        handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    else:
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.WARNING)
