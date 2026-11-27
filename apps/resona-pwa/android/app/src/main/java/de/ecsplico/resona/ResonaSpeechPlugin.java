package de.ecsplico.resona;

import android.Manifest;
import android.content.Intent;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionSupport;
import android.speech.RecognitionSupportCallback;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.util.Base64;

import androidx.annotation.ChecksSdkIntAtLeast;
import androidx.annotation.NonNull;
import androidx.annotation.RequiresApi;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import de.ecsplico.resona.speech.LanguageSupport;
import de.ecsplico.resona.speech.LiveSpeechSession;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * On-device live dictation for the live page ({@code app/composables/useDeviceSpeech.ts},
 * {@code app/utils/resonaSpeech.ts}): Android 13+ (API 33) only, older devices answer
 * {@code unsupported}.
 *
 * <ul>
 *   <li>{@code available({language})} → {@code {status}}: {@code available}, {@code downloadable},
 *   {@code downloading}, {@code unavailable} or {@code unsupported}, from the on-device
 *   recognizer's {@code RecognitionSupport}.</li>
 *   <li>{@code install({language})} → {@code {started}}: asks the system to download the
 *   language pack ({@code triggerModelDownload}).</li>
 *   <li>{@code start({language, emitPcm})}: asks for the microphone if needed, then runs a
 *   {@link LiveSpeechSession}. Events: {@code update {committed, tentative, revision}},
 *   {@code pcm {data}} (base64 little-endian int16, 16 kHz mono, 250 ms per event) and
 *   {@code error {code, message, fatal}}.</li>
 *   <li>{@code stop()} → {@code {text}}: the whole on-device text, after the last
 *   {@code pcm} event.</li>
 * </ul>
 *
 * The web app starts the microphone foreground service before {@code start()}; with it the
 * capture (and so the session) keeps running while the screen is locked.
 */
@CapacitorPlugin(
    name = "ResonaSpeech",
    permissions = { @Permission(alias = "microphone", strings = { Manifest.permission.RECORD_AUDIO }) }
)
public class ResonaSpeechPlugin extends Plugin {

    private static final long SUPPORT_TIMEOUT_MS = 5000;
    private static final long DOWNLOAD_RECOGNIZER_LINGER_MS = 10_000;

    private final Handler main = new Handler(Looper.getMainLooper());
    // stop() blocks until the recognizer is done; it must hold neither the main looper
    // nor Capacitor's plugin thread.
    private final ExecutorService stopper = Executors.newSingleThreadExecutor();
    private LiveSpeechSession session;

    @ChecksSdkIntAtLeast(api = Build.VERSION_CODES.TIRAMISU)
    private static boolean supported() {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU;
    }

    @PluginMethod
    public void available(PluginCall call) {
        String language = call.getString("language", "");
        if (!supported()) {
            resolveStatus(call, "unsupported");
            return;
        }
        main.post(() -> checkSupport(call, language));
    }

    @RequiresApi(Build.VERSION_CODES.TIRAMISU)
    private void checkSupport(PluginCall call, String language) {
        if (!SpeechRecognizer.isOnDeviceRecognitionAvailable(getContext())) {
            resolveStatus(call, "unavailable");
            return;
        }
        SpeechRecognizer probe = SpeechRecognizer.createOnDeviceSpeechRecognizer(getContext());
        AtomicBoolean done = new AtomicBoolean(false);
        Runnable finish = () -> {
            // Callbacks arrive on the main looper; the probe is released there too.
            probe.destroy();
        };
        main.postDelayed(() -> {
            if (done.compareAndSet(false, true)) {
                finish.run();
                resolveStatus(call, "unavailable");
            }
        }, SUPPORT_TIMEOUT_MS);
        probe.checkRecognitionSupport(recognizeIntent(language), getContext().getMainExecutor(), new RecognitionSupportCallback() {
            @Override
            public void onSupportResult(@NonNull RecognitionSupport support) {
                if (!done.compareAndSet(false, true)) return;
                finish.run();
                resolveStatus(call, LanguageSupport.status(
                    language,
                    support.getInstalledOnDeviceLanguages(),
                    support.getPendingOnDeviceLanguages(),
                    support.getSupportedOnDeviceLanguages()
                ));
            }

            @Override
            public void onError(int error) {
                if (!done.compareAndSet(false, true)) return;
                finish.run();
                // A recognizer that cannot report its languages may still recognize this one:
                // let the user try; a missing language then shows as an `error` event.
                resolveStatus(call, error == SpeechRecognizer.ERROR_CANNOT_CHECK_SUPPORT ? "available" : "unavailable");
            }
        });
    }

    @PluginMethod
    public void install(PluginCall call) {
        String language = call.getString("language", "");
        if (!supported()) {
            JSObject result = new JSObject();
            result.put("started", false);
            call.resolve(result);
            return;
        }
        main.post(() -> {
            if (!SpeechRecognizer.isOnDeviceRecognitionAvailable(getContext())) {
                JSObject result = new JSObject();
                result.put("started", false);
                call.resolve(result);
                return;
            }
            SpeechRecognizer downloader = SpeechRecognizer.createOnDeviceSpeechRecognizer(getContext());
            downloader.triggerModelDownload(recognizeIntent(language));
            // The request goes out once the recognizer service is bound; release it later.
            main.postDelayed(downloader::destroy, DOWNLOAD_RECOGNIZER_LINGER_MS);
            JSObject result = new JSObject();
            result.put("started", true);
            call.resolve(result);
        });
    }

    @PluginMethod
    public void start(PluginCall call) {
        if (!supported()) {
            call.reject("Lokale Spracherkennung braucht Android 13 oder neuer.", "unsupported");
            return;
        }
        if (getPermissionState("microphone") != PermissionState.GRANTED) {
            requestPermissionForAlias("microphone", call, "microphonePermission");
            return;
        }
        begin(call);
    }

    @PermissionCallback
    private void microphonePermission(PluginCall call) {
        if (getPermissionState("microphone") == PermissionState.GRANTED && supported()) {
            begin(call);
        }
        else {
            call.reject("Mikrofon nicht erlaubt", "not-allowed");
        }
    }

    @RequiresApi(Build.VERSION_CODES.TIRAMISU)
    private synchronized void begin(PluginCall call) {
        if (session != null) {
            session.abort();
            session = null;
        }
        String language = call.getString("language", "de-DE");
        boolean emitPcm = Boolean.TRUE.equals(call.getBoolean("emitPcm", true));
        LiveSpeechSession[] self = new LiveSpeechSession[1];
        LiveSpeechSession next = new LiveSpeechSession(getContext(), language, emitPcm, new LiveSpeechSession.Listener() {
            @Override
            public void onUpdate(String committed, String tentative, int revision) {
                if (!isCurrent(self[0])) return;
                JSObject event = new JSObject();
                event.put("committed", committed);
                event.put("tentative", tentative);
                event.put("revision", revision);
                notifyListeners("update", event);
            }

            @Override
            public void onPcm(byte[] frame) {
                if (!isCurrent(self[0])) return;
                JSObject event = new JSObject();
                event.put("data", Base64.encodeToString(frame, Base64.NO_WRAP));
                notifyListeners("pcm", event);
            }

            @Override
            public void onError(String code, String message, boolean fatal) {
                if (!isCurrent(self[0])) return;
                JSObject event = new JSObject();
                event.put("code", code);
                event.put("message", message);
                event.put("fatal", fatal);
                notifyListeners("error", event);
            }
        });
        self[0] = next;
        session = next;
        try {
            next.start();
        }
        catch (Exception e) {
            session = null;
            call.reject(e.getMessage(), "audio-capture");
            return;
        }
        call.resolve();
    }

    /** Only the current session talks to the web app; an aborted one may still be draining. */
    private synchronized boolean isCurrent(LiveSpeechSession candidate) {
        return candidate != null && session == candidate;
    }

    @PluginMethod
    public void stop(PluginCall call) {
        LiveSpeechSession ending;
        synchronized (this) {
            ending = session;
        }
        JSObject result = new JSObject();
        if (ending == null || !supported()) {
            result.put("text", "");
            call.resolve(result);
            return;
        }
        stopper.execute(() -> {
            String text;
            try {
                text = ending.stop();
            }
            catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                text = "";
            }
            synchronized (this) {
                if (session == ending) session = null;
            }
            result.put("text", text);
            call.resolve(result);
        });
    }

    @Override
    protected void handleOnDestroy() {
        synchronized (this) {
            if (session != null && supported()) session.abort();
            session = null;
        }
        stopper.shutdown();
    }

    private static Intent recognizeIntent(String language) {
        Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, language);
        return intent;
    }

    private static void resolveStatus(PluginCall call, String status) {
        JSObject result = new JSObject();
        result.put("status", status);
        call.resolve(result);
    }
}
