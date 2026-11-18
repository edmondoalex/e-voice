package it.ekonex.voice.watch;
import android.content.Intent;
import android.os.Handler;
import android.os.Looper;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;
public final class WearMessagingService extends FirebaseMessagingService {
 @Override public void onNewToken(String token){super.onNewToken(token);getSharedPreferences("ekonex_wear",MODE_PRIVATE).edit().putString("fcm_token",token).apply();FcmRegistration.sync(this);}
 @Override public void onMessageReceived(RemoteMessage message){super.onMessageReceived(message);if("wear_notification".equals(message.getData().get("type")))new Handler(Looper.getMainLooper()).postDelayed(()->startForegroundService(new Intent(this,NotificationPollService.class)),1200L);}
}
