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
 * anonymised, so the audio session id is the only handle). Of the recordings that appear
 * afterwards only microphone ones count, which is how the recorder plugin records.
 * Event {@code silencedChanged {silenced}}: on every change, and again on every
 * configuration change while silenced, so a recording resumed during the call is reported
 * again. Used by {@code app/composables/useNativeRecorder.ts}.
 */
@CapacitorPlugin(name = "RecordingSilence")
public class RecordingSilencePlugin extends Plugin {

    private final Set<Integer> foreignSessions = new HashSet<>();
    private AudioManager.AudioRecordingCallback callback;
    private boolean silenced = false;

    @PluginMethod
    public void start(PluginCall call) {
        JSObject result = new JSObject();
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            result.put("supported", false);
            call.resolve(result);
            return;
        }
        register(audioManager());
        result.put("supported", true);
        call.resolve(result);
    }

    @RequiresApi(Build.VERSION_CODES.Q)
    private void register(AudioManager audio) {
        unregister(audio);
        foreignSessions.clear();
        for (AudioRecordingConfiguration config : audio.getActiveRecordingConfigurations()) {
            foreignSessions.add(config.getClientAudioSessionId());
        }
        silenced = false;
        callback = new AudioManager.AudioRecordingCallback() {
            @Override
            @RequiresApi(Build.VERSION_CODES.Q)
            public void onRecordingConfigChanged(List<AudioRecordingConfiguration> configs) {
                update(configs);
            }
        };
        audio.registerAudioRecordingCallback(callback, new Handler(Looper.getMainLooper()));
    }

    @PluginMethod
    public void stop(PluginCall call) {
        unregister(audioManager());
        call.resolve();
    }

    @Override
    protected void handleOnDestroy() {
        unregister(audioManager());
    }

    @RequiresApi(Build.VERSION_CODES.Q)
    private void update(List<AudioRecordingConfiguration> configs) {
        boolean now = false;
        for (AudioRecordingConfiguration config : configs) {
            if (foreignSessions.contains(config.getClientAudioSessionId())) continue;
            if (config.getClientAudioSource() != MediaRecorder.AudioSource.MIC) continue;
            if (config.isClientSilenced()) {
                now = true;
                break;
            }
        }
        if (!now && !silenced) return;
        silenced = now;
        JSObject event = new JSObject();
        event.put("silenced", now);
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
