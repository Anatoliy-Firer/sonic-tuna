([WIDTH, HEIGHT]) => {

    const SCAN_INTERVAL_MS = 1000;
    const SIGNATURE_DISTANCE_THRESHOLD = 15;
    const SIGNATURE_LT = [255, 255, 255];
    const SIGNATURE_RT = [0, 0, 0];
    const SIGNATURE_LB = [0, 0, 0];
    const SIGNATURE_RB = [255, 255, 255];

    function fillRgbFromRgba(rgbaBytes, rgbBytes) {
        if (WIDTH <= 0 || HEIGHT <= 0) {
            return false;
        }
        const pixelAt = (x, y) => {
            const src = (y * WIDTH + x) * 4;
            return [rgbaBytes[src], rgbaBytes[src + 1], rgbaBytes[src + 2]];
        };

        const distance = (actual, expected) => {
            const dR = expected[0] - actual[0];
            const dG = expected[1] - actual[1];
            const dB = expected[2] - actual[2];
            return Math.hypot(dR, dG, dB);
        };

        const lt = pixelAt(0, 0);
        const rt = pixelAt(0, HEIGHT - 1);
        const lb = pixelAt(WIDTH - 1, 0);
        const rb = pixelAt(WIDTH - 1, HEIGHT - 1);

        const isValid = distance(lt, SIGNATURE_LT) <= SIGNATURE_DISTANCE_THRESHOLD
            && distance(rt, SIGNATURE_RT) <= SIGNATURE_DISTANCE_THRESHOLD
            && distance(lb, SIGNATURE_LB) <= SIGNATURE_DISTANCE_THRESHOLD
            && distance(rb, SIGNATURE_RB) <= SIGNATURE_DISTANCE_THRESHOLD;

        if (!isValid) return false;

        for (let y = 0; y < HEIGHT; y += 1) {
            const rgbaRowOffset = y * WIDTH * 4;
            const rgbRowOffset = y * WIDTH;

            for (let x = 0; x < WIDTH; x += 1) {
                const src = rgbaRowOffset + x * 4;
                const dst = rgbRowOffset + x;
                const red = rgbaBytes[src];
                const green = rgbaBytes[src + 1];
                const blue = rgbaBytes[src + 2];

                rgbBytes[dst] = (red + green + blue) / 3;
            }
        }

        return true;
    }

    function getRemoteVideoEntries() {
        const videos = Array.from(document.getElementsByTagName("video"));
        const entries = [];

        videos.forEach((video, index) => {
            if (index === 0) {
                return;
            }

            const stream = video.srcObject;
            if (!(stream instanceof MediaStream) || !stream.active) {
                return;
            }

            const [track] = stream.getVideoTracks();
            if (!track || track.readyState !== "live") {
                return;
            }

            entries.push({
                sourceId: `${stream.id}:${track.id}`,
                stream,
                track,
                video
            });
        });

        return entries;
    }

    function getFrameBridge() {
        const bridge = globalThis.__sonicTunaFrameBridge;
        if (!bridge || typeof bridge.sendFrame !== "function") {
            throw new Error("Shared frame bridge is unavailable");
        }

        return bridge;
    }

    async function streamRemoteVideo(entry, abortController) {
        const frameBridge = getFrameBridge();
        const processor = new MediaStreamTrackProcessor({track: entry.track});
        const reader = processor.readable.getReader();
        const canvas = new OffscreenCanvas(WIDTH, HEIGHT);
        const ctx = canvas.getContext("2d", {willReadFrequently: true});
        ctx.imageSmoothingEnabled = false;
        const rgbBytes = new Uint8Array(WIDTH * HEIGHT);

        const stopReader = async () => {
            try {
                await reader.cancel();
            } catch (_) {
            }
        };

        entry.track.addEventListener("ended", () => abortController.abort(), {once: true});
        abortController.signal.addEventListener("abort", () => {
            stopReader();
        }, {once: true});

        while (!abortController.signal.aborted) {
            const {value: frame, done} = await reader.read();
            if (done) {
                break;
            }

            try {
                ctx.drawImage(frame, 0, 0, WIDTH, HEIGHT);
                const rgbaBytes = ctx.getImageData(0, 0, WIDTH, HEIGHT).data;
                if (!fillRgbFromRgba(rgbaBytes, rgbBytes)) {
                    continue;
                }
                frameBridge.sendFrame(rgbBytes);
            } finally {
                frame.close();
            }
        }
    }

    const activeCaptures = new Map();
    let scanScheduled = false;

    function stopCapture(sourceId) {
        const capture = activeCaptures.get(sourceId);
        if (!capture) {
            return;
        }

        capture.abortController.abort();
        activeCaptures.delete(sourceId);
    }

    function scheduleScan() {
        if (scanScheduled) {
            return;
        }

        scanScheduled = true;
        queueMicrotask(() => {
            scanScheduled = false;
            syncCaptures();
        });
    }

    function syncCaptures() {
        const remoteEntries = getRemoteVideoEntries();
        const nextIds = new Set(remoteEntries.map((entry) => entry.sourceId));

        for (const sourceId of activeCaptures.keys()) {
            if (!nextIds.has(sourceId)) {
                stopCapture(sourceId);
            }
        }

        for (const entry of remoteEntries) {
            if (activeCaptures.has(entry.sourceId)) {
                continue;
            }

            const abortController = new AbortController();
            const runPromise = streamRemoteVideo(entry, abortController).catch((error) => {
                if (error?.name !== "AbortError") {
                    console.error(error);
                }
            }).finally(() => {
                const current = activeCaptures.get(entry.sourceId);
                if (current?.abortController === abortController) {
                    activeCaptures.delete(entry.sourceId);
                    scheduleScan();
                }
            });

            activeCaptures.set(entry.sourceId, {
                abortController,
                runPromise
            });
        }
    }

    const observer = new MutationObserver(() => {
        scheduleScan();
    });

    if (document.body) {
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ["srcObject", "class", "style"]
        });
    }

    setInterval(syncCaptures, SCAN_INTERVAL_MS);
    syncCaptures();
}
