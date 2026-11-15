package de.ecsplico.resona;

import android.content.Context;
import android.media.AudioManager;
import android.media.AudioRecordingConfiguration;
import android.media.MediaRecorder;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;

import androidx.annotation.RequiresApi;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Tells the web app when Android silences its microphone recording. During a phone call
 * (Android 10+ concurrent capture policy) the recording client gets silence and the
 * recorder plugin notices nothing; {@link AudioRecordingConfiguration#isClientSilenced()}
 * does. No READ_PHONE_STATE: this sees any silencing, not only calls.
 *
 * <p>{@code start()} runs right before the recorder starts: recordings active at that
 * moment belong to other apps and are ignored (configurations reach non-system apps
 * anonymised, so the audio session id is the only handle). The first microphone recording
 * that appears afterwards is ours: its session id is pinned and only it is judged, so an
 * app that starts recording later does not count. Event
 * {@code silencedChanged {silenced, at}} ({@code at}: wall-clock ms of the change, as the
 * web view may deliver it much later): on every change, and again on every configuration
 * change while silenced, so a recording resumed during the call is reported again. All
 * state lives on the main looper, where the callback runs. Used by
 * {@code app/composables/useNativeRecorder.ts}.
 */
@CapacitorPlugin(name = "RecordingSilence")
public class RecordingSilencePlugin extends Plugin {

    private final Handler main = new Handler(Looper.getMainLooper());
    private final Set<Integer> foreignSessions = new HashSet<>();
    private Integer ownSession = null;
    private AudioManager.AudioRecordingCallback callback;
    private boolean silenced = false;

    @PluginMethod
    public void start(PluginCall call) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            JSObject result = new JSObject();
            result.put("supported", false);
            call.resolve(result);
            return;
        }
        main.post(() -> {
            register(audioManager());
            JSObject result = new JSObject();
            result.put("supported", true);
            call.resolve(result);
        });
    }

    @RequiresApi(Build.VERSION_CODES.Q)
    private void register(AudioManager audio) {
        unregister(audio);
        foreignSessions.clear();
        for (AudioRecordingConfiguration config : audio.getActiveRecordingConfigurations()) {
            foreignSessions.add(config.getClientAudioSessionId());
        }
        ownSession = null;
        callback = new AudioManager.AudioRecordingCallback() {
            @Override
            @RequiresApi(Build.VERSION_CODES.Q)
            public void onRecordingConfigChanged(List<AudioRecordingConfiguration> configs) {
                // A change queued before stop()/start() replaced this registration belongs to
                // the previous recorder: it must not pin that recorder's session.
                if (callback != this) return;
                update(configs);
            }
        };
        audio.registerAudioRecordingCallback(callback, main);
    }

    @PluginMethod
    public void stop(PluginCall call) {
        main.post(() -> {
            unregister(audioManager());
            call.resolve();
        });
    }

    @Override
    protected void handleOnDestroy() {
        main.post(() -> unregister(audioManager()));
    }

    @RequiresApi(Build.VERSION_CODES.Q)
    private void update(List<AudioRecordingConfiguration> configs) {
        if (ownSession == null) {
            for (AudioRecordingConfiguration config : configs) {
                int session = config.getClientAudioSessionId();
                if (foreignSessions.contains(session)) continue;
                if (config.getClientAudioSource() != MediaRecorder.AudioSource.MIC) continue;
                ownSession = session;
                break;
            }
            if (ownSession == null) return;
        }
        boolean now = false;
        for (AudioRecordingConfiguration config : configs) {
            if (config.getClientAudioSessionId() == ownSession && config.isClientSilenced()) {
                now = true;
                break;
            }
        }
        if (!now && !silenced) return;
        silenced = now;
        JSObject event = new JSObject();
        event.put("silenced", now);
        event.put("at", System.currentTimeMillis());
        notifyListeners("silencedChanged", event);
    }

    private void unregister(AudioManager audio) {
        if (callback != null) {
            audio.unregisterAudioRecordingCallback(callback);
            callback = null;
        }
    }

    private AudioManager audioManager() {
        return (AudioManager) getContext().getSystemService(Context.AUDIO_SERVICE);
    }
}
