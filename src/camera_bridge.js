const WIDTH = INPUT_W * SCALE_FACTOR;
const HEIGHT = INPUT_H * SCALE_FACTOR;

const sourceCanvas = new OffscreenCanvas(INPUT_W, INPUT_H);
const FRAME_INTERVAL_MS = 500 / FPS;
const MAX_OUTGOING_FRAMES = 20;

const canvas = document.createElement("canvas");
canvas.width = WIDTH;
canvas.height = HEIGHT;

const ctx = canvas.getContext("2d", {alpha: false});
const sourceCtx = sourceCanvas.getContext("2d", {alpha: false});
ctx.imageSmoothingEnabled = false;
sourceCtx.imageSmoothingEnabled = false;
const stream = canvas.captureStream(0);
const [videoTrack] = stream.getVideoTracks();
const frameQueue = [];
const frameQueueLimit = 10;
const outgoingFrameQueue = [];
let socket = null;

let emptyFrameAngle = 0.0;

function drawEmptyFrame() {
    const red = Math.round(((Math.cos(emptyFrameAngle) + 1) / 2) * 255);
    const blue = Math.round(((Math.sin(emptyFrameAngle) + 1) / 2) * 255);
    let color = `rgb(${red} 0 ${blue})`;
    emptyFrameAngle += 0.01;

    ctx.fillStyle = color;
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
    ctx.fillStyle = '#ffffff';
    ctx.fillText("SONIC TUNA RULES!", 10, 10);
    videoTrack.requestFrame?.();
}

function drawFrame(rgbaBytes) {
    if (!(rgbaBytes instanceof Uint8ClampedArray) || rgbaBytes.length !== INPUT_W * INPUT_H * 4) {
        return;
    }

    const imageData = new ImageData(rgbaBytes, INPUT_W, INPUT_H);
    sourceCtx.putImageData(imageData, 0, 0);
    ctx.drawImage(sourceCanvas, 0, 0, WIDTH, HEIGHT);
    videoTrack.requestFrame?.();
}

function buildRgbaVariants(grayBytes) {
    if (!(grayBytes instanceof Uint8Array) || grayBytes.length !== INPUT_W * INPUT_H) {
        return [];
    }

    const blackVariant = new Uint8ClampedArray(INPUT_W * INPUT_H * 4);
    const whiteVariant = new Uint8ClampedArray(INPUT_W * INPUT_H * 4);
    const squareSize = 4;

    for (let i = 0; i < grayBytes.length; i += 1) {
        const val = grayBytes[i];
        const rgbaIndex = i * 4;

        blackVariant[rgbaIndex] = val;
        blackVariant[rgbaIndex + 1] = val;
        blackVariant[rgbaIndex + 2] = val;
        blackVariant[rgbaIndex + 3] = 255;

        whiteVariant[rgbaIndex] = val;
        whiteVariant[rgbaIndex + 1] = val;
        whiteVariant[rgbaIndex + 2] = val;
        whiteVariant[rgbaIndex + 3] = 255;
    }

    for (let y = 0; y < Math.min(squareSize, INPUT_H); y += 1) {
        for (let x = 0; x < Math.min(squareSize, INPUT_W); x += 1) {
            const rgbaIndex = (y * INPUT_W + x) * 4;
            blackVariant[rgbaIndex] = 0;
            blackVariant[rgbaIndex + 1] = 0;
            blackVariant[rgbaIndex + 2] = 0;

            whiteVariant[rgbaIndex] = 255;
            whiteVariant[rgbaIndex + 1] = 255;
            whiteVariant[rgbaIndex + 2] = 255;
        }
    }

    return [blackVariant, whiteVariant];
}

function tick() {
    const nextFrame = frameQueue.shift();
    if (nextFrame) {
        drawFrame(nextFrame);
        return;
    }

    drawEmptyFrame();
}

function flushOutgoingFrames() {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
        return;
    }

    while (outgoingFrameQueue.length > 0 && socket.readyState === WebSocket.OPEN) {
        socket.send(outgoingFrameQueue.shift());
    }
}

function sendFrame(frameBytes) {
    if (!(frameBytes instanceof Uint8Array) || frameBytes.length !== INPUT_W * INPUT_H) {
        return false;
    }

    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(frameBytes);
        return true;
    }

    outgoingFrameQueue.push(new Uint8Array(frameBytes));
    while (outgoingFrameQueue.length > MAX_OUTGOING_FRAMES) {
        outgoingFrameQueue.shift();
    }

    return false;
}

function connectFrames() {
    const currentSocket = new WebSocket(WS_URL);
    currentSocket.binaryType = "arraybuffer";
    socket = currentSocket;

    currentSocket.onopen = () => {
        flushOutgoingFrames();
    };

    currentSocket.onmessage = (event) => {
        const variants = buildRgbaVariants(new Uint8Array(event.data));
        frameQueue.push(...variants);
        while (frameQueue.length > frameQueueLimit) frameQueue.shift();
    };

    currentSocket.onclose = () => {
        if (socket === currentSocket) {
            socket = null;
        }
        setTimeout(connectFrames, 1000);
    };

    currentSocket.onerror = () => {
        currentSocket.close();
    };
}

globalThis.__sonicTunaFrameBridge = {
    sendFrame
};

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
