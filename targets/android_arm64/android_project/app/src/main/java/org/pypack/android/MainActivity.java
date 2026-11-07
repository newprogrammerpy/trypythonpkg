package org.pypack.android;

import android.app.Activity;
import android.content.res.AssetManager;
import android.os.Bundle;
import android.widget.TextView;
import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private TextView statusView;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        statusView = new TextView(this);
        statusView.setText("Starting Python...");
        setContentView(statusView);
        executor.execute(() -> {
            try {
                File home = new File(getFilesDir(), "python");
                copyAssets(getAssets(), "python", home);
                System.loadLibrary("python_runner");
                int status = runPython(home.getAbsolutePath(),
                        new File(home, "app.py").getAbsolutePath());
                runOnUiThread(() -> statusView.setText("Python exited with status " + status));
            } catch (Exception error) {
                runOnUiThread(() -> statusView.setText("Python startup failed: " + error));
            }
        });
    }

    @Override
    protected void onDestroy() {
        executor.shutdownNow();
        super.onDestroy();
    }

    private static void copyAssets(AssetManager assets, String assetPath, File destination)
            throws IOException {
        String[] children = assets.list(assetPath);
        if (children == null) {
            throw new IOException("Unable to list packaged Python assets: " + assetPath);
        }
        if (!destination.isDirectory() && !destination.mkdirs()) {
            throw new IOException("Unable to create Python runtime directory: " + destination);
        }
        for (String child : children) {
            String childAsset = assetPath + "/" + child;
            File childFile = new File(destination, child);
            String[] grandchildren = assets.list(childAsset);
            if (grandchildren == null) {
                throw new IOException("Unable to list packaged Python assets: " + childAsset);
            }
            if (grandchildren.length > 0) {
                copyAssets(assets, childAsset, childFile);
                continue;
            }
            try (InputStream input = assets.open(childAsset);
                 FileOutputStream output = new FileOutputStream(childFile)) {
                byte[] buffer = new byte[8192];
                int count;
                while ((count = input.read(buffer)) != -1) {
                    output.write(buffer, 0, count);
                }
            } catch (FileNotFoundException error) {
                if (!childFile.isDirectory() && !childFile.mkdirs()) {
                    throw new IOException("Unable to create Python runtime directory: " + childFile, error);
                }
            }
        }
    }

    private static native int runPython(String home, String script);
}
