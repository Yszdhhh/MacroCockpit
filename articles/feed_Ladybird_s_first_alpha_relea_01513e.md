---
title: "Ladybird's first alpha release"
summary: "Ladybird is an astonishingly ambitious project: a new browser, in 2026, with its own implementation of most of the key components - HTML parsing, CSS engine, layout, JavaScript, even WebAssembly. I..."
publisher: "Simon Willison"
url: "https://simonwillison.net/2026/Nov/23/ladybird/"
score: 67
---

[Ladybird](https://ladybird.org) is an astonishingly ambitious project: a new browser, in 2026, with its own implementation of most of the key components - HTML parsing, CSS engine, layout, JavaScript, even WebAssembly. It still uses a few external components - things like [HarfBuzz](https://github.com/harfbuzz/harfbuzz) for text shaping, [Skia](https://skia.org/) for 2D graphics, ffmpeg for video, and SQLite for the things you do with SQLite. Everything else is part of Ladybird, or code brought in from Ladybird's even more ambitious predecessor project [SerenityOS](https://serenityos.org/).

This morning the Ladybird project [released their first alpha](https://ladybird.org/posts/alpha/), which means you can now [install the browser](https://ladybird.org/download/) (on macOS or Linux) without having to compile it yourself!

I've been trying it out today, and it's very impressive. It feels fast, the UI is lightweight and intuitive, and most of the websites I've tried to load in it have worked as they should. It looks like this (showing my [video-compressor](https://tools.simonwillison.net/video-compressor) tool):

Here's the process tree after I opened six tabs in one window and another tab in a second window (generated [by this script](https://gist.github.com/simonw/b9162f1741b96fea72b11c300b749226), thanks Claude):

Ladybird (72925) [240 MB]
└── Hub (72926) [61 MB]
├── RequestServer (72927) [29 MB]
├── ProcessReaper (72928) [1 MB]
├── Compositor (72930) [123 MB]
├── RequestServer (72953) [36 MB]
├── WebContent (72984) [819 MB]
├── MediaServer (73006) [118 MB]
├── VideoDecoderServer (73007) [20 MB]
├── WebContent (73052) [144 MB]
├── WebContent (73057) [73 MB]
├── WebContent (73073) [166 MB]
├── WebContent (73094) [70 MB]
├── WebContent (73112) [116 MB]
├── RequestServer (73113) [27 MB]
└── WebContent (73185) [60 MB]
Ladybird runs different tabs in different processes, an isolation mechanism used by most browsers (well, technically Chrome uses a process-per-site).

I tried Ladybird against several of my more advanced tool examples - like the [video-compressor](https://tools.simonwillison.net/video-compressor) shown above, which runs ffmpeg compiled to WebAssembly. I had it resize its own screenshot using my [image-resize tool](https://tools.simonwillison.net/image-resize-quality). It happily runs my [Pyodide REPL](https://tools.simonwillison.net/pyodide-repl) (Python compiled to WebAssembly) too.

The one thing I tried that didn't work was [Datasette Lite](https://lite.datasette.io/), which runs Pyodide in [a complicated arrangement](https://simonwillison.net/2026/Nov/17/datasette-lite/#how-this-works-using-service-workers) of service workers and web workers. Attempting to start Datasette Lite displays this error:

> undefined is not a function (evaluated from 'navigator.serviceWorker.startMessages')

I filed [an issue about this](https://github.com/LadybirdBrowser/ladybird/issues/15515), then found [a FIXME annotation](https://github.com/LadybirdBrowser/ladybird/blob/864f4362b5334a7e830c4c7c923a95292a871d4e/Libraries/LibWeb/ServiceWorker/ServiceWorkerContainer.idl#L12) in the source code.

#### The October explosion

The project has been leaning heavily into coding agents this year. On February 23, 2026 Andreas Kling published [Ladybird adopts Rust, with help from AI](https://ladybird.org/posts/adopting-rust/), describing how they ported parts of their LibJS JavaScript engine from C++ to 25,000 lines of Rust in two weeks using Claude Code and Codex.

Here's [the GitHub contributions chart](https://github.com/LadybirdBrowser/ladybird/graphs/contributors?from=2025-11-22T08%3A00%3A00.000Z) for Ladybird over the past twelve months:

That uptick in October corresponds to the landing of a wealth of big improvements.

[This Month in Ladybird — October 2026](https://ladybird.org/newsletter/2026-10-31/) describes what happened. In addition to moving style and layout to their own thread, landing a new JavaScript JIT, and replacing their C++ with Cranelift WebAssembly engine with new code that achieved a 7x boost on the [JetStream 3 WASM benchmark](https://webkit.org/blog/17899/introducing-the-jetstream-3-benchmark-suite/), they gained 75,650 new passes on the ~2.25 million subtest [web-platform-tests](https://web-platform-tests.org/) suite, beating Safari!

How did they do that? Coding agents:

> Most of that gain came in five days, from about 2.12 million on October 22 to about 2.18 million on October 27. We pointed AI coding agents at WPT and asked them to fill in missing functionality. They made lots of small fixes, and those added up. Our biggest gains for the month were about 30,000 in /css and 10,000 in /editing .

I'm interested in what models they are using here - I know Andreas uses Codex and Claude Code, but I haven't seen him talk about specific model versions. Claude Opus 5.5 came out on September 22, 2026 and Claude Fable 5.2 on November 9, 2026 (after that window). I'd love to know what they were using for that huge leap in the last week of October.

#### Should you use Ladybird?

Here's what [Andreas says](https://ladybird.org/posts/alpha/) about Ladybird's suitability for use as a daily browser:

> This is an alpha, so some websites won’t work, and it will crash sometimes. When that happens, tell us! You can report a broken site from inside the browser. After a crash, you can read the crash report and decide whether to send it. Our [privacy policy](https://ladybird.org/privacy/) has the details. We want security nerds to hack it! A new browser gets hacked, over and over, and that’s part of the process, so we make no promises about security today. When you pwn us, please [tell us how](https://github.com/LadybirdBrowser/ladybird/blob/master/SECURITY.md) so we can harden Ladybird!

Given how hard it is to secure a web browser, and the hostility of the environment they operate in, I won't be using Ladybird to sign into GitHub or Gmail or any of my other sensitive accounts.

I'm going to keep it around for anonymous browsing though, partly to participate in the testing efforts but mainly because it gives me a glimmer of optimism to see a promising new entrant in the stagnant browser engine market start to grow its wings.

You are only seeing the long-form articles from my blog. Subscribe to [/atom/everything/](https://simonwillison.net/atom/everything/) to get all of my posts, or take a look at my [other subscription options](https://simonwillison.net/about/#subscribe).
