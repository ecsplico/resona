package de.ecsplico.resona;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        // App-local plugins register before the bridge starts.
        registerPlugin(RecordingSilencePlugin.class);
        super.onCreate(savedInstanceState);
    }
}
