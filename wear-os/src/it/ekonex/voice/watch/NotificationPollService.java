package it.ekonex.voice.watch;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

public final class NotificationPollService extends Service {
    private static final String API="__EKONEX_WEAR_API_BASE__";
    private static final String PREFS="ekonex_wear";
    private static final String KEY_ALIAS="ekonex_wear_device_v1";
    private static final String STATUS_CHANNEL="ekonex_wear_status";
    private static final String EVENT_CHANNEL="ekonex_wear_events";
    private static final long POLL_INTERVAL_MS=60000L;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private volatile boolean polling;

    @Override public void onCreate(){super.onCreate();createChannels();startForeground(1001,statusNotification());handler.post(this::poll);}
    @Override public int onStartCommand(Intent intent,int flags,int startId){handler.removeCallbacksAndMessages(null);handler.post(this::poll);return START_STICKY;}
    @Override public IBinder onBind(Intent intent){return null;}
    @Override public void onDestroy(){handler.removeCallbacksAndMessages(null);super.onDestroy();}

    private void createChannels(){NotificationManager manager=getSystemService(NotificationManager.class);manager.createNotificationChannel(new NotificationChannel(STATUS_CHANNEL,"Servizio notifiche Ekonex",NotificationManager.IMPORTANCE_LOW));manager.createNotificationChannel(new NotificationChannel(EVENT_CHANNEL,"Notifiche Ekonex",NotificationManager.IMPORTANCE_HIGH));}
    private PendingIntent openApp(){Intent intent=new Intent(this,MainActivity.class);intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_CLEAR_TOP);return PendingIntent.getActivity(this,0,intent,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);}
    private Notification statusNotification(){return new Notification.Builder(this,STATUS_CHANNEL).setSmallIcon(getApplicationInfo().icon).setContentTitle("Ekonex Wear").setContentText("Notifiche attive").setOngoing(true).setContentIntent(openApp()).build();}
    private void poll(){if(polling)return;polling=true;new Thread(()->{try{String credential=loadCredential();if(credential.isEmpty()){stopSelf();return;}JSONObject response=get(API+"/notifications",credential);JSONArray items=response.getJSONArray("notifications");for(int index=0;index<items.length();index++){JSONObject item=items.getJSONObject(index);show(item);ack(item.getString("id"),credential);}}catch(Exception ignored){}finally{polling=false;handler.postDelayed(this::poll,POLL_INTERVAL_MS);}}).start();}
    private void show(JSONObject item)throws Exception{String id=item.getString("id");String title=item.getString("title");String body=item.getString("body");Notification notification=new Notification.Builder(this,EVENT_CHANNEL).setSmallIcon(getApplicationInfo().icon).setContentTitle(title).setContentText(body).setStyle(new Notification.BigTextStyle().bigText(body)).setAutoCancel(true).setContentIntent(openApp()).build();getSystemService(NotificationManager.class).notify(id.hashCode(),notification);}
    private JSONObject get(String endpoint,String token)throws Exception{HttpURLConnection connection=(HttpURLConnection)new URL(endpoint).openConnection();try{connection.setConnectTimeout(6000);connection.setReadTimeout(12000);connection.setRequestProperty("Authorization","Bearer "+token);if(connection.getResponseCode()!=200)throw new IllegalStateException();StringBuilder value=new StringBuilder();try(BufferedReader reader=new BufferedReader(new InputStreamReader(connection.getInputStream(),StandardCharsets.UTF_8))){String line;while((line=reader.readLine())!=null&&value.length()<32768)value.append(line);}return new JSONObject(value.toString());}finally{connection.disconnect();}}
    private void ack(String id,String token)throws Exception{HttpURLConnection connection=(HttpURLConnection)new URL(API+"/notifications/"+id+"/ack").openConnection();try{connection.setRequestMethod("POST");connection.setConnectTimeout(6000);connection.setReadTimeout(12000);connection.setRequestProperty("Authorization","Bearer "+token);connection.setDoOutput(true);connection.getOutputStream().close();if(connection.getResponseCode()!=204)throw new IllegalStateException();}finally{connection.disconnect();}}
    private String loadCredential(){try{String iv=getSharedPreferences(PREFS,MODE_PRIVATE).getString("iv","");String encrypted=getSharedPreferences(PREFS,MODE_PRIVATE).getString("credential","");if(iv.isEmpty()||encrypted.isEmpty())return"";KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);SecretKey key=((KeyStore.SecretKeyEntry)store.getEntry(KEY_ALIAS,null)).getSecretKey();Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key,new GCMParameterSpec(128,Base64.decode(iv,Base64.NO_WRAP)));return new String(cipher.doFinal(Base64.decode(encrypted,Base64.NO_WRAP)),StandardCharsets.UTF_8);}catch(Exception error){return"";}}
}
