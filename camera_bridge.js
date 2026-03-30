(() => {
    const WIDTH = 256;
    const HEIGHT = 256;
    const FPS = 30;
    const FRAME_INTERVAL_MS = 1000 / FPS;
    const WS_URL = "ws://localhost:8000/frame-stream";

    const canvas = document.createElement("canvas");
    canvas.width = WIDTH;
    canvas.height = HEIGHT;

    const ctx = canvas.getContext("2d", {alpha: false});
    const stream = canvas.captureStream(FPS);
    const [videoTrack] = stream.getVideoTracks();
    const frameQueue = [];

    function drawEmptyFrame() {
        ctx.fillStyle = "#000000";
        ctx.fillRect(0, 0, WIDTH, HEIGHT);
    }

    function drawFrame(rgbBytes) {
        if (!(rgbBytes instanceof Uint8Array) || rgbBytes.length !== WIDTH * HEIGHT * 3) {
            return;
        }

        const rgbaBytes = new Uint8ClampedArray(WIDTH * HEIGHT * 4);
        for (let src = 0, dst = 0; src < rgbBytes.length; src += 3, dst += 4) {
            rgbaBytes[dst] = rgbBytes[src];
            rgbaBytes[dst + 1] = rgbBytes[src + 1];
            rgbaBytes[dst + 2] = rgbBytes[src + 2];
            rgbaBytes[dst + 3] = 255;
        }

        ctx.putImageData(new ImageData(rgbaBytes, WIDTH, HEIGHT), 0, 0);
    }

    function tick() {
        const nextFrame = frameQueue.shift();
        if (nextFrame) {
            drawFrame(nextFrame);
            return;
        }

        drawEmptyFrame();
    }

    function connectFrames() {
        const socket = new WebSocket(WS_URL);
        socket.binaryType = "arraybuffer";

        socket.onmessage = (event) => {
            frameQueue.push(new Uint8Array(event.data));
        };

        socket.onclose = () => {
            setTimeout(connectFrames, 1000);
        };

        socket.onerror = () => {
            socket.close();
        };
    }

    drawEmptyFrame();
    setInterval(tick, FRAME_INTERVAL_MS);
    connectFrames();

    const originalGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);

    navigator.mediaDevices.getUserMedia = async (constraints) => {
        const needsVideo = Boolean(constraints && constraints.video);
        if (!needsVideo) {
            return originalGetUserMedia(constraints);
        }

        const originalStream = await originalGetUserMedia(constraints);
        const outgoing = new MediaStream();

        originalStream.getAudioTracks().forEach((track) => outgoing.addTrack(track));
        outgoing.addTrack(videoTrack.clone());

        originalStream.getVideoTracks().forEach((track) => track.stop());

        return outgoing;
    };
})();
