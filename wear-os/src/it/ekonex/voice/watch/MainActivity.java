package it.ekonex.voice.watch;

import android.app.Activity;
import android.app.AlertDialog;
import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.speech.RecognizerIntent;
import android.speech.RecognitionListener;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.text.InputFilter;
import android.text.InputType;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.style.AbsoluteSizeSpan;
import android.text.style.ForegroundColorSpan;
import android.text.style.TypefaceSpan;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.GridLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.ArrayList;
import java.util.Locale;
import java.util.Properties;

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
    private EditText code; private TextView result; private Button associate; private Button microphone; private FrameLayout quickActions; private GridLayout extraActions;
    private TextToSpeech textToSpeech; private boolean speechReady; private String pendingSpeech;
    private SpeechRecognizer speechRecognizer; private String partialUtterance="";
    private Typeface mdiTypeface; private final Properties mdiCodepoints=new Properties();
    private final Handler stateHandler=new Handler(Looper.getMainLooper());
    private final Runnable stateRefresh=new Runnable(){public void run(){if(!loadCredential().isEmpty())new Thread(()->{loadBootstrap();runOnUiThread(()->stateHandler.postDelayed(this,15000));}).start();}};

    @Override protected void onCreate(Bundle state){super.onCreate(state); getWindow().getDecorView().setBackgroundColor(Color.BLACK); loadMdiCatalog();
        LinearLayout content=new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL); content.setGravity(Gravity.CENTER); content.setPadding(24,24,24,24);
        code=new EditText(this); code.setHint("Codice a 6 cifre"); code.setSingleLine(true); code.setInputType(InputType.TYPE_CLASS_NUMBER); code.setFilters(new InputFilter[]{new InputFilter.LengthFilter(6)}); code.setTextColor(Color.WHITE); code.setHintTextColor(Color.GRAY); content.addView(code,new LinearLayout.LayoutParams(-1,-2));
        associate=new Button(this); associate.setText("Associa"); associate.setOnClickListener(v->claim()); content.addView(associate,new LinearLayout.LayoutParams(-1,-2));
        microphone=new Button(this); microphone.setAllCaps(false); microphone.setText("Ekonex"); microphone.setTextSize(9); microphone.setGravity(Gravity.CENTER); microphone.setPadding(dp(4),dp(5),dp(4),dp(4)); microphone.setCompoundDrawablePadding(dp(1)); microphone.setTextColor(Color.WHITE); microphone.setCompoundDrawablesWithIntrinsicBounds(0,getResources().getIdentifier("microphone","drawable",getPackageName()),0,0); microphone.setOnClickListener(v->listen());
        quickActions=new FrameLayout(this);quickActions.setClipChildren(false);quickActions.setClipToPadding(false);content.addView(quickActions,new LinearLayout.LayoutParams(-1,dp(206))); extraActions=new GridLayout(this);extraActions.setColumnCount(4);extraActions.setAlignmentMode(GridLayout.ALIGN_BOUNDS);content.addView(extraActions,new LinearLayout.LayoutParams(-1,-2));
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
    private FrameLayout.LayoutParams radialCell(int size,int angle,int radius){FrameLayout.LayoutParams params=new FrameLayout.LayoutParams(dp(size),dp(size),Gravity.CENTER);double radians=Math.toRadians(angle);params.leftMargin=(int)Math.round(Math.cos(radians)*dp(radius));params.topMargin=(int)Math.round(Math.sin(radians)*dp(radius));return params;}
    private void resetActionGrid(){quickActions.removeAllViews();GradientDrawable center=new GradientDrawable();center.setShape(GradientDrawable.OVAL);center.setColor(Color.rgb(20,25,29));center.setStroke(dp(2),Color.rgb(229,0,30));microphone.setBackground(center);microphone.setEnabled(!loadCredential().isEmpty());quickActions.addView(microphone,radialCell(66,0,0));}
    private void refresh(){boolean paired=!loadCredential().isEmpty(); code.setVisibility(paired?View.GONE:View.VISIBLE); associate.setVisibility(paired?View.GONE:View.VISIBLE);resetActionGrid(); if(paired){result.setText("Carico i comandi rapidi...");enableNotifications();new Thread(this::loadBootstrap).start();}}
    private void loadBootstrap(){try{JSONObject bootstrap=get(API+"/bootstrap",loadCredential());JSONArray actions=bootstrap.optJSONArray("quick_actions");runOnUiThread(()->renderQuickActions(actions));}catch(Exception error){show("Orologio associato. Comandi rapidi non disponibili; il microfono resta attivo.");}}
    private JSONObject actionForSlot(JSONArray actions,int slot){for(int i=0;i<actions.length();i++){JSONObject action=actions.optJSONObject(i);if(action!=null&&action.optInt("slot",0)==slot)return action;}return null;}
    private Button actionButton(JSONObject action,int slot,int size){Button button=new Button(this);button.setAllCaps(false);button.setGravity(Gravity.CENTER);button.setPadding(dp(2),dp(3),dp(2),dp(2));button.setMaxLines(2);GradientDrawable background=new GradientDrawable();background.setShape(GradientDrawable.OVAL);if(action==null){button.setText("");button.setEnabled(false);background.setColor(Color.rgb(55,59,62));background.setStroke(dp(1),Color.rgb(105,112,116));}else{boolean confirmation=action.optBoolean("confirmation",false);boolean confirmationOnActivate=action.optBoolean("confirmation_on_activate",false)&&action.optBoolean("activating_action",false);String label=action.optString("label","Comando"),color=action.optString("color");button.setTextColor(actionTextColor(color));button.setTextSize(size<=42?6:7);applyMdiIcon(button,action.optString("icon"),label,action.optString("state_visual","unknown"),size);background.setColor("unavailable".equals(action.optString("entity_state"))?Color.rgb(65,65,65):actionColor(color));background.setStroke(dp(1),Color.WHITE);button.setOnClickListener(v->{if(confirmation||confirmationOnActivate)new AlertDialog.Builder(this).setTitle("Conferma comando").setMessage("Eseguire "+label+"?").setNegativeButton("Annulla",null).setPositiveButton("Esegui",(dialog,which)->executeQuickAction(slot,button)).show();else executeQuickAction(slot,button);});}button.setBackground(background);return button;}
    private void renderQuickActions(JSONArray actions){JSONArray configured=actions==null?new JSONArray():actions;resetActionGrid();extraActions.removeAllViews();int[] angles={-90,-45,0,45,90,135,180,225};for(int index=0;index<8;index++){int slot=index+1;quickActions.addView(actionButton(actionForSlot(configured,slot),slot,48),radialCell(48,angles[index],72));}for(int slot=9;slot<=32;slot++){JSONObject action=actionForSlot(configured,slot);if(action==null)continue;Button button=actionButton(action,slot,42);GridLayout.LayoutParams params=new GridLayout.LayoutParams();params.width=dp(42);params.height=dp(42);params.setMargins(dp(4),dp(3),dp(4),dp(3));extraActions.addView(button,params);}result.setText("");}
    private void loadMdiCatalog(){try{int fontId=getResources().getIdentifier("mdi_webfont","font",getPackageName());int mapId=getResources().getIdentifier("mdi_codepoints","raw",getPackageName());mdiTypeface=getResources().getFont(fontId);try(InputStream input=getResources().openRawResource(mapId)){mdiCodepoints.load(input);}}catch(Exception error){mdiTypeface=Typeface.DEFAULT;}}
    private String mdiName(String iconName){String value=iconName==null?"":iconName.trim().toLowerCase(Locale.ROOT);if(value.startsWith("mdi:"))return value.substring(4);if("light".equals(value))return "lightbulb";if("power".equals(value))return "power";if("climate".equals(value))return "thermostat";if("blind".equals(value))return "blinds";if("lock".equals(value))return "shield-home";if("music".equals(value))return "music";return "creation";}
    private int stateIconColor(String visual){if("light_on".equals(visual))return Color.rgb(255,193,7);if("cover_open".equals(visual))return Color.rgb(73,211,242);if("lock_closed".equals(visual))return Color.rgb(35,190,95);if("lock_open".equals(visual))return Color.rgb(235,55,45);return Color.LTGRAY;}
    private void applyMdiIcon(Button button,String iconName,String label,String visual,int size){String code=mdiCodepoints.getProperty(mdiName(iconName));if(code==null)code=mdiCodepoints.getProperty("help-circle-outline");String glyph=new String(Character.toChars(Integer.parseInt(code,16)));SpannableString rendered=new SpannableString(glyph+"\n"+label);rendered.setSpan(new TypefaceSpan(mdiTypeface),0,glyph.length(),Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);rendered.setSpan(new AbsoluteSizeSpan(size<=42?18:22,true),0,glyph.length(),Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);rendered.setSpan(new ForegroundColorSpan(stateIconColor(visual)),0,glyph.length(),Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);button.setText(rendered);}
    private int actionTextColor(String value){return "yellow".equals(value)||"white".equals(value)||"lime".equals(value)||"amber".equals(value)?Color.rgb(20,24,27):Color.WHITE;}
    private int actionColor(String value){if("red".equals(value))return Color.rgb(190,35,35);if("green".equals(value))return Color.rgb(0,125,75);if("orange".equals(value))return Color.rgb(190,95,0);if("purple".equals(value))return Color.rgb(105,55,160);if("cyan".equals(value))return Color.rgb(0,120,145);if("yellow".equals(value))return Color.rgb(245,205,45);if("pink".equals(value))return Color.rgb(205,55,125);if("white".equals(value))return Color.rgb(225,230,235);if("gray".equals(value))return Color.rgb(85,92,98);if("teal".equals(value))return Color.rgb(0,135,125);if("lime".equals(value))return Color.rgb(155,205,55);if("amber".equals(value))return Color.rgb(245,165,35);return Color.rgb(25,95,190);}
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
    @Override protected void onResume(){super.onResume();stateHandler.removeCallbacks(stateRefresh);stateHandler.postDelayed(stateRefresh,15000);}
    @Override protected void onPause(){stateHandler.removeCallbacks(stateRefresh);super.onPause();}
    @Override protected void onDestroy(){stateHandler.removeCallbacks(stateRefresh);if(speechRecognizer!=null)speechRecognizer.destroy();if(textToSpeech!=null){textToSpeech.stop();textToSpeech.shutdown();}super.onDestroy();}
    @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);acknowledgeNotification(intent);}
    private void acknowledgeNotification(Intent intent){String notificationId=intent.getStringExtra("notification_id");if(notificationId==null||notificationId.isEmpty())return;intent.removeExtra("notification_id");new Thread(()->{try{ackNotification(notificationId,loadCredential());}catch(Exception ignored){}}).start();}
    private void ackNotification(String notificationId,String token)throws Exception{HttpURLConnection connection=(HttpURLConnection)new URL(API+"/notifications/"+notificationId+"/ack").openConnection();try{connection.setRequestMethod("POST");connection.setConnectTimeout(6000);connection.setReadTimeout(12000);connection.setRequestProperty("Authorization","Bearer "+token);connection.setDoOutput(true);connection.getOutputStream().close();if(connection.getResponseCode()!=204)throw new IllegalStateException("HTTP "+connection.getResponseCode());}finally{connection.disconnect();}}
    private JSONObject post(String endpoint,JSONObject body,String token)throws Exception{HttpURLConnection connection=(HttpURLConnection)new URL(endpoint).openConnection(); try{connection.setRequestMethod("POST");connection.setConnectTimeout(6000);connection.setReadTimeout(12000);connection.setRequestProperty("Content-Type","application/json; charset=utf-8");if(token!=null)connection.setRequestProperty("Authorization","Bearer "+token);connection.setDoOutput(true);try(OutputStream output=connection.getOutputStream()){output.write(body.toString().getBytes(StandardCharsets.UTF_8));}int status=connection.getResponseCode();if(status!=200)throw new IllegalStateException("HTTP "+status);StringBuilder value=new StringBuilder();try(BufferedReader reader=new BufferedReader(new InputStreamReader(connection.getInputStream(),StandardCharsets.UTF_8))){String line;while((line=reader.readLine())!=null&&value.length()<16384)value.append(line);}return new JSONObject(value.toString());}finally{connection.disconnect();}}
    private JSONObject get(String endpoint,String token)throws Exception{HttpURLConnection connection=(HttpURLConnection)new URL(endpoint).openConnection();try{connection.setRequestMethod("GET");connection.setConnectTimeout(6000);connection.setReadTimeout(12000);connection.setRequestProperty("Authorization","Bearer "+token);int status=connection.getResponseCode();if(status!=200)throw new IllegalStateException("HTTP "+status);StringBuilder value=new StringBuilder();try(BufferedReader reader=new BufferedReader(new InputStreamReader(connection.getInputStream(),StandardCharsets.UTF_8))){String line;while((line=reader.readLine())!=null&&value.length()<32768)value.append(line);}return new JSONObject(value.toString());}finally{connection.disconnect();}}
    private SecretKey key()throws Exception{KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);if(store.containsAlias(KEY_ALIAS))return((KeyStore.SecretKeyEntry)store.getEntry(KEY_ALIAS,null)).getSecretKey();KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");generator.init(new KeyGenParameterSpec.Builder(KEY_ALIAS,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());return generator.generateKey();}
    private void saveCredential(String value)throws Exception{Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key());String iv=Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP);String encrypted=Base64.encodeToString(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP);getSharedPreferences(PREFS,MODE_PRIVATE).edit().putString("iv",iv).putString("credential",encrypted).apply();}
    private String loadCredential(){try{String iv=getSharedPreferences(PREFS,MODE_PRIVATE).getString("iv","");String encrypted=getSharedPreferences(PREFS,MODE_PRIVATE).getString("credential","");if(iv.isEmpty()||encrypted.isEmpty())return"";Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(iv,Base64.NO_WRAP)));return new String(cipher.doFinal(Base64.decode(encrypted,Base64.NO_WRAP)),StandardCharsets.UTF_8);}catch(Exception error){return"";}}
}
