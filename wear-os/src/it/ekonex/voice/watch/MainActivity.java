package it.ekonex.voice.watch;

import android.app.Activity;
import android.app.AlertDialog;
import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.Drawable;
import android.os.Bundle;
import android.os.Build;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.speech.RecognizerIntent;
import android.speech.RecognitionListener;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.text.InputFilter;
import android.text.InputType;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.GridLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.ArrayList;
import java.util.Locale;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

public final class MainActivity extends Activity {
    private static final int MICROPHONE_PERMISSION_REQUEST=1;
    private static final int NOTIFICATION_PERMISSION_REQUEST=2;
    private static final String API="__EKONEX_WEAR_API_BASE__";
    private static final String PREFS="ekonex_wear";
    private static final String KEY_ALIAS="ekonex_wear_device_v1";
    private EditText code; private TextView result; private Button associate; private Button microphone; private GridLayout quickActions;
    private TextToSpeech textToSpeech; private boolean speechReady; private String pendingSpeech;
    private SpeechRecognizer speechRecognizer; private String partialUtterance="";

    @Override protected void onCreate(Bundle state){super.onCreate(state); getWindow().getDecorView().setBackgroundColor(Color.BLACK);
        LinearLayout content=new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL); content.setGravity(Gravity.CENTER); content.setPadding(24,24,24,24);
        code=new EditText(this); code.setHint("Codice a 6 cifre"); code.setSingleLine(true); code.setInputType(InputType.TYPE_CLASS_NUMBER); code.setFilters(new InputFilter[]{new InputFilter.LengthFilter(6)}); code.setTextColor(Color.WHITE); code.setHintTextColor(Color.GRAY); content.addView(code,new LinearLayout.LayoutParams(-1,-2));
        associate=new Button(this); associate.setText("Associa"); associate.setOnClickListener(v->claim()); content.addView(associate,new LinearLayout.LayoutParams(-1,-2));
        microphone=new Button(this); microphone.setAllCaps(false); microphone.setText("Ekonex\ne-Voice"); microphone.setTextSize(11); microphone.setTextColor(Color.WHITE); microphone.setCompoundDrawablesWithIntrinsicBounds(0,getResources().getIdentifier("microphone","drawable",getPackageName()),0,0); microphone.setOnClickListener(v->listen());
        quickActions=new GridLayout(this);quickActions.setColumnCount(3);quickActions.setRowCount(3);quickActions.setAlignmentMode(GridLayout.ALIGN_BOUNDS);content.addView(quickActions,new LinearLayout.LayoutParams(-1,dp(245)));
        result=text("Associa l'orologio da e-Control.",13); content.addView(result);
        ScrollView scroll=new ScrollView(this); scroll.setFillViewport(true); scroll.addView(content); setContentView(scroll); refresh();
        if(SpeechRecognizer.isRecognitionAvailable(this)){speechRecognizer=SpeechRecognizer.createSpeechRecognizer(this);speechRecognizer.setRecognitionListener(new RecognitionListener(){
            public void onReadyForSpeech(Bundle params){partialUtterance="";result.setText("Ti ascolto...");}
            public void onBeginningOfSpeech(){result.setText("Sto ascoltando...");}
            public void onRmsChanged(float rmsdB){}
            public void onBufferReceived(byte[] buffer){}
            public void onEndOfSpeech(){result.setText("Attendo e-Voice...");}
            public void onError(int error){microphone.setEnabled(true);if(!partialUtterance.isEmpty()){String utterance=partialUtterance;result.setText("Ho capito: "+utterance+"\nAttendo e-Voice...");new Thread(()->ask(utterance)).start();return;}result.setText("Non ho capito (errore "+error+"). Tocca e riprova.");}
            public void onResults(Bundle results){microphone.setEnabled(true);ArrayList<String> phrases=results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);String utterance="";if(phrases!=null)for(String phrase:phrases)if(phrase!=null&&!phrase.trim().isEmpty()){utterance=phrase.trim();break;}if(utterance.isEmpty())utterance=partialUtterance;if(utterance.isEmpty()){result.setText("Non ho capito. Tocca e riprova.");return;}String request=utterance;result.setText("Ho capito: "+request+"\nAttendo e-Voice...");new Thread(()->ask(request)).start();}
            public void onPartialResults(Bundle partialResults){ArrayList<String> phrases=partialResults.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);if(phrases!=null&&!phrases.isEmpty()&&phrases.get(0)!=null&&!phrases.get(0).trim().isEmpty()){partialUtterance=phrases.get(0).trim();result.setText("Ho sentito: "+partialUtterance);}}
            public void onEvent(int eventType,Bundle params){}
        });}
        textToSpeech=new TextToSpeech(this,status->{if(status!=TextToSpeech.SUCCESS)return; int language=textToSpeech.setLanguage(Locale.ITALIAN); speechReady=language!=TextToSpeech.LANG_MISSING_DATA&&language!=TextToSpeech.LANG_NOT_SUPPORTED; if(speechReady&&pendingSpeech!=null){String message=pendingSpeech;pendingSpeech=null;speak(message);}});
        acknowledgeNotification(getIntent());
    }
    private int dp(int value){return Math.round(value*getResources().getDisplayMetrics().density);}
    private TextView text(String value,float size){TextView view=new TextView(this); view.setText(value); view.setTextColor(Color.WHITE); view.setTextSize(size); view.setGravity(Gravity.CENTER); view.setPadding(0,6,0,6); return view;}
    private GridLayout.LayoutParams cell(int row,int column){GridLayout.LayoutParams params=new GridLayout.LayoutParams(GridLayout.spec(row,1,1f),GridLayout.spec(column,1,1f));params.width=0;params.height=0;params.setGravity(Gravity.FILL);params.setMargins(dp(3),dp(3),dp(3),dp(3));return params;}
    private void resetActionGrid(){quickActions.removeAllViews();GradientDrawable center=new GradientDrawable();center.setShape(GradientDrawable.OVAL);center.setColor(Color.rgb(20,25,29));center.setStroke(dp(2),Color.rgb(229,0,30));microphone.setBackground(center);microphone.setEnabled(!loadCredential().isEmpty());quickActions.addView(microphone,cell(1,1));}
    private void refresh(){boolean paired=!loadCredential().isEmpty(); code.setVisibility(paired?View.GONE:View.VISIBLE); associate.setVisibility(paired?View.GONE:View.VISIBLE);resetActionGrid(); if(paired){result.setText("Carico i comandi rapidi...");enableNotifications();new Thread(this::loadBootstrap).start();}}
    private void loadBootstrap(){try{JSONObject bootstrap=get(API+"/bootstrap",loadCredential());JSONArray actions=bootstrap.optJSONArray("quick_actions");runOnUiThread(()->renderQuickActions(actions));}catch(Exception error){show("Orologio associato. Comandi rapidi non disponibili; il microfono resta attivo.");}}
    private void renderQuickActions(JSONArray actions){resetActionGrid();if(actions==null||actions.length()==0){result.setText("Configura i comandi rapidi in e-Control.");return;}int[][] positions={{0,0},{0,2},{1,2},{2,2},{2,0},{1,0}};int rendered=0;for(int i=0;i<actions.length()&&rendered<positions.length;i++){JSONObject action=actions.optJSONObject(i);if(action==null)continue;int slot=action.optInt("slot",0);boolean confirmation=action.optBoolean("confirmation",false);boolean confirmationOnActivate=action.optBoolean("confirmation_on_activate",false)&&action.optBoolean("activating_action",false);String iconName=action.optString("icon");String visual=action.optString("state_visual","unknown");Button button=new Button(this);button.setAllCaps(false);button.setText(action.optString("label","Comando"));button.setTextColor(Color.WHITE);button.setTextSize(9);button.setGravity(Gravity.CENTER);applyStateIcon(button,iconName,visual);GradientDrawable background=new GradientDrawable();background.setShape(GradientDrawable.OVAL);background.setColor(actionColor(action.optString("color")));background.setStroke(dp(1),Color.WHITE);button.setBackground(background);int[] position=positions[rendered++];quickActions.addView(button,cell(position[0],position[1]));button.setOnClickListener(v->{if(confirmation||confirmationOnActivate)new AlertDialog.Builder(this).setTitle("Conferma comando").setMessage("Eseguire "+button.getText()+"?").setNegativeButton("Annulla",null).setPositiveButton("Esegui",(dialog,which)->executeQuickAction(slot,button)).show();else executeQuickAction(slot,button);});}result.setText("Pronto");}
    private int stateIconResource(String iconName){String asset="light".equals(iconName)?"state_light":"blind".equals(iconName)?"state_blind":"lock".equals(iconName)?"state_lock":"power".equals(iconName)?"state_power":"climate".equals(iconName)?"state_climate":"music".equals(iconName)?"state_music":"state_scene";return getResources().getIdentifier(asset,"drawable",getPackageName());}
    private void applyStateIcon(Button button,String iconName,String visual){int resource=stateIconResource(iconName);if(resource==0)return;Drawable drawable=getDrawable(resource).mutate();int color=Color.GRAY;if("light_on".equals(visual))color=Color.rgb(255,193,7);else if("cover_open".equals(visual))color=Color.rgb(73,211,242);else if("lock_closed".equals(visual))color=Color.rgb(35,190,95);else if("lock_open".equals(visual))color=Color.rgb(235,55,45);drawable.setTint(color);int size=(int)(28*getResources().getDisplayMetrics().density);drawable.setBounds(0,0,size,size);button.setCompoundDrawablePadding(12);button.setCompoundDrawables(drawable,null,null,null);}
    private int actionColor(String value){if("red".equals(value))return Color.rgb(190,35,35);if("green".equals(value))return Color.rgb(0,125,75);if("orange".equals(value))return Color.rgb(190,95,0);if("purple".equals(value))return Color.rgb(105,55,160);if("cyan".equals(value))return Color.rgb(0,120,145);return Color.rgb(25,95,190);}
    private void executeQuickAction(int slot,Button button){button.setEnabled(false);result.setText("Eseguo "+button.getText()+"...");new Thread(()->{try{JSONObject response=post(API+"/quick-actions/"+slot+"/execute",new JSONObject(),loadCredential());showAndSpeak(response.getString("speech"));Thread.sleep(1200);loadBootstrap();}catch(Exception error){show("Comando rapido non riuscito. Verifica connessione, stato e autorizzazioni.");}finally{runOnUiThread(()->button.setEnabled(true));}}).start();}
    private void enableNotifications(){if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},NOTIFICATION_PERMISSION_REQUEST);return;}startForegroundService(new Intent(this,NotificationPollService.class));}
    private void claim(){String value=code.getText().toString().trim(); if(!value.matches("\\d{6}")){result.setText("Inserisci le 6 cifre.");return;} associate.setEnabled(false); result.setText("Associazione in corso..."); new Thread(()->requestClaim(value)).start();}
    private void requestClaim(String value){try{JSONObject body=new JSONObject().put("code",value).put("display_name",android.os.Build.MODEL); JSONObject response=post(API+"/enrollments/claim",body,null); saveCredential(response.getString("credential")); runOnUiThread(()->{code.setText("");associate.setEnabled(true);refresh();});}catch(Exception error){runOnUiThread(()->{associate.setEnabled(true);result.setText("Associazione non riuscita. Genera un nuovo codice in e-Control.");});}}
    private void listen(){if(checkSelfPermission(Manifest.permission.RECORD_AUDIO)!=PackageManager.PERMISSION_GRANTED){requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO},MICROPHONE_PERMISSION_REQUEST);return;}startListening();}
    private void startListening(){if(speechRecognizer==null){result.setText("Riconoscimento vocale non disponibile.");return;}if(textToSpeech!=null)textToSpeech.stop();microphone.setEnabled(false);Intent intent=new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE,Locale.ITALIAN.toLanguageTag());intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS,5);intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS,true);intent.putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE,false);speechRecognizer.startListening(intent);}
    @Override public void onRequestPermissionsResult(int requestCode,String[] permissions,int[] grantResults){super.onRequestPermissionsResult(requestCode,permissions,grantResults);if(requestCode==NOTIFICATION_PERMISSION_REQUEST){if(grantResults.length>0&&grantResults[0]==PackageManager.PERMISSION_GRANTED)enableNotifications();return;}if(requestCode!=MICROPHONE_PERMISSION_REQUEST)return;if(grantResults.length>0&&grantResults[0]==PackageManager.PERMISSION_GRANTED)startListening();else result.setText("Abilita il microfono per parlare con e-Voice.");}
    private void ask(String utterance){try{JSONObject response=post(API+"/ask",new JSONObject().put("utterance",utterance),loadCredential()); showAndSpeak(response.getString("speech"));}catch(Exception error){show("e-Voice non disponibile o autorizzazione revocata.");}}
    private void show(String message){runOnUiThread(()->result.setText(message));}
    private void showAndSpeak(String message){runOnUiThread(()->{result.setText(message);speak(message);});}
    private void speak(String message){if(textToSpeech==null||!speechReady){pendingSpeech=message;return;}textToSpeech.speak(message,TextToSpeech.QUEUE_FLUSH,null,"ekonex_voice_reply");}
    @Override protected void onDestroy(){if(speechRecognizer!=null)speechRecognizer.destroy();if(textToSpeech!=null){textToSpeech.stop();textToSpeech.shutdown();}super.onDestroy();}
    @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);acknowledgeNotification(intent);}
    private void acknowledgeNotification(Intent intent){String notificationId=intent.getStringExtra("notification_id");if(notificationId==null||notificationId.isEmpty())return;intent.removeExtra("notification_id");new Thread(()->{try{ackNotification(notificationId,loadCredential());}catch(Exception ignored){}}).start();}
    private void ackNotification(String notificationId,String token)throws Exception{HttpURLConnection connection=(HttpURLConnection)new URL(API+"/notifications/"+notificationId+"/ack").openConnection();try{connection.setRequestMethod("POST");connection.setConnectTimeout(6000);connection.setReadTimeout(12000);connection.setRequestProperty("Authorization","Bearer "+token);connection.setDoOutput(true);connection.getOutputStream().close();if(connection.getResponseCode()!=204)throw new IllegalStateException("HTTP "+connection.getResponseCode());}finally{connection.disconnect();}}
    private JSONObject post(String endpoint,JSONObject body,String token)throws Exception{HttpURLConnection connection=(HttpURLConnection)new URL(endpoint).openConnection(); try{connection.setRequestMethod("POST");connection.setConnectTimeout(6000);connection.setReadTimeout(12000);connection.setRequestProperty("Content-Type","application/json; charset=utf-8");if(token!=null)connection.setRequestProperty("Authorization","Bearer "+token);connection.setDoOutput(true);try(OutputStream output=connection.getOutputStream()){output.write(body.toString().getBytes(StandardCharsets.UTF_8));}int status=connection.getResponseCode();if(status!=200)throw new IllegalStateException("HTTP "+status);StringBuilder value=new StringBuilder();try(BufferedReader reader=new BufferedReader(new InputStreamReader(connection.getInputStream(),StandardCharsets.UTF_8))){String line;while((line=reader.readLine())!=null&&value.length()<16384)value.append(line);}return new JSONObject(value.toString());}finally{connection.disconnect();}}
    private JSONObject get(String endpoint,String token)throws Exception{HttpURLConnection connection=(HttpURLConnection)new URL(endpoint).openConnection();try{connection.setRequestMethod("GET");connection.setConnectTimeout(6000);connection.setReadTimeout(12000);connection.setRequestProperty("Authorization","Bearer "+token);int status=connection.getResponseCode();if(status!=200)throw new IllegalStateException("HTTP "+status);StringBuilder value=new StringBuilder();try(BufferedReader reader=new BufferedReader(new InputStreamReader(connection.getInputStream(),StandardCharsets.UTF_8))){String line;while((line=reader.readLine())!=null&&value.length()<32768)value.append(line);}return new JSONObject(value.toString());}finally{connection.disconnect();}}
    private SecretKey key()throws Exception{KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);if(store.containsAlias(KEY_ALIAS))return((KeyStore.SecretKeyEntry)store.getEntry(KEY_ALIAS,null)).getSecretKey();KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");generator.init(new KeyGenParameterSpec.Builder(KEY_ALIAS,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());return generator.generateKey();}
    private void saveCredential(String value)throws Exception{Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key());String iv=Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP);String encrypted=Base64.encodeToString(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP);getSharedPreferences(PREFS,MODE_PRIVATE).edit().putString("iv",iv).putString("credential",encrypted).apply();}
    private String loadCredential(){try{String iv=getSharedPreferences(PREFS,MODE_PRIVATE).getString("iv","");String encrypted=getSharedPreferences(PREFS,MODE_PRIVATE).getString("credential","");if(iv.isEmpty()||encrypted.isEmpty())return"";Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(iv,Base64.NO_WRAP)));return new String(cipher.doFinal(Base64.decode(encrypted,Base64.NO_WRAP)),StandardCharsets.UTF_8);}catch(Exception error){return"";}}
}
