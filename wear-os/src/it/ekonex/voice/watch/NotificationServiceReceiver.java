package it.ekonex.voice.watch;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

public final class NotificationServiceReceiver extends BroadcastReceiver {
    public static final String ACTION_RESTART="it.ekonex.voice.watch.RESTART_NOTIFICATIONS";

    @Override public void onReceive(Context context,Intent intent){
        if(!hasCredential(context))return;
        Intent service=new Intent(context,NotificationPollService.class);
        if(Build.VERSION.SDK_INT>=26)context.startForegroundService(service);else context.startService(service);
    }

    private boolean hasCredential(Context context){
        return !context.getSharedPreferences("ekonex_wear",Context.MODE_PRIVATE).getString("credential","").isEmpty();
    }
}
