package it.ekonex.voice.watch;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.speech.RecognizerIntent;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

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
    private static final int SPEECH_REQUEST=1;
    private static final String API="__EKONEX_WEAR_API_BASE__";
    private static final String PREFS="ekonex_wear";
    private static final String KEY_ALIAS="ekonex_wear_device_v1";
    private EditText code; private TextView result; private Button associate; private Button microphone;

    @Override protected void onCreate(Bundle state){super.onCreate(state); getWindow().getDecorView().setBackgroundColor(Color.BLACK);
        LinearLayout content=new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL); content.setGravity(Gravity.CENTER); content.setPadding(24,24,24,24);
        TextView title=text("Ekonex Wear",23); content.addView(title);
        code=new EditText(this); code.setHint("Codice associazione"); code.setSingleLine(true); code.setTextColor(Color.WHITE); code.setHintTextColor(Color.GRAY); content.addView(code,new LinearLayout.LayoutParams(-1,-2));
        associate=new Button(this); associate.setText("Associa"); associate.setOnClickListener(v->claim()); content.addView(associate,new LinearLayout.LayoutParams(-1,-2));
        microphone=new Button(this); microphone.setText("Parla con e-Voice"); microphone.setOnClickListener(v->listen()); content.addView(microphone,new LinearLayout.LayoutParams(-1,-2));
        result=text("Associa l'orologio da e-Control.",15); content.addView(result);
        ScrollView scroll=new ScrollView(this); scroll.setFillViewport(true); scroll.addView(content); setContentView(scroll); refresh();
    }
    private TextView text(String value,float size){TextView view=new TextView(this); view.setText(value); view.setTextColor(Color.WHITE); view.setTextSize(size); view.setGravity(Gravity.CENTER); view.setPadding(0,10,0,10); return view;}
    private void refresh(){boolean paired=!loadCredential().isEmpty(); code.setVisibility(paired?View.GONE:View.VISIBLE); associate.setVisibility(paired?View.GONE:View.VISIBLE); microphone.setEnabled(paired); if(paired) result.setText("Orologio associato. Tocca Parla con e-Voice.");}
    private void claim(){String value=code.getText().toString().trim(); if(value.length()<20){result.setText("Codice non valido.");return;} associate.setEnabled(false); result.setText("Associazione in corso..."); new Thread(()->requestClaim(value)).start();}
    private void requestClaim(String value){try{JSONObject body=new JSONObject().put("code",value).put("display_name",android.os.Build.MODEL); JSONObject response=post(API+"/enrollments/claim",body,null); saveCredential(response.getString("credential")); runOnUiThread(()->{code.setText("");associate.setEnabled(true);refresh();});}catch(Exception error){runOnUiThread(()->{associate.setEnabled(true);result.setText("Associazione non riuscita. Genera un nuovo codice in e-Control.");});}}
    private void listen(){Intent intent=new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH); intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,RecognizerIntent.LANGUAGE_MODEL_FREE_FORM); intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE,Locale.ITALIAN.toLanguageTag()); intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS,1); try{startActivityForResult(intent,SPEECH_REQUEST);}catch(ActivityNotFoundException error){result.setText("Riconoscimento vocale non disponibile.");}}
    @Override protected void onActivityResult(int request,int outcome,Intent data){super.onActivityResult(request,outcome,data); if(request!=SPEECH_REQUEST||outcome!=RESULT_OK||data==null)return; ArrayList<String> phrases=data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS); if(phrases==null||phrases.isEmpty())return; String utterance=phrases.get(0).trim(); result.setText("Attendo e-Voice..."); new Thread(()->ask(utterance)).start();}
    private void ask(String utterance){try{JSONObject response=post(API+"/ask",new JSONObject().put("utterance",utterance),loadCredential()); show(response.getString("speech"));}catch(Exception error){show("e-Voice non disponibile o autorizzazione revocata.");}}
    private void show(String message){runOnUiThread(()->result.setText(message));}
    private JSONObject post(String endpoint,JSONObject body,String token)throws Exception{HttpURLConnection connection=(HttpURLConnection)new URL(endpoint).openConnection(); try{connection.setRequestMethod("POST");connection.setConnectTimeout(6000);connection.setReadTimeout(12000);connection.setRequestProperty("Content-Type","application/json; charset=utf-8");if(token!=null)connection.setRequestProperty("Authorization","Bearer "+token);connection.setDoOutput(true);try(OutputStream output=connection.getOutputStream()){output.write(body.toString().getBytes(StandardCharsets.UTF_8));}int status=connection.getResponseCode();if(status!=200)throw new IllegalStateException("HTTP "+status);StringBuilder value=new StringBuilder();try(BufferedReader reader=new BufferedReader(new InputStreamReader(connection.getInputStream(),StandardCharsets.UTF_8))){String line;while((line=reader.readLine())!=null&&value.length()<16384)value.append(line);}return new JSONObject(value.toString());}finally{connection.disconnect();}}
    private SecretKey key()throws Exception{KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);if(store.containsAlias(KEY_ALIAS))return((KeyStore.SecretKeyEntry)store.getEntry(KEY_ALIAS,null)).getSecretKey();KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");generator.init(new KeyGenParameterSpec.Builder(KEY_ALIAS,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());return generator.generateKey();}
    private void saveCredential(String value)throws Exception{Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key());String iv=Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP);String encrypted=Base64.encodeToString(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP);getSharedPreferences(PREFS,MODE_PRIVATE).edit().putString("iv",iv).putString("credential",encrypted).apply();}
    private String loadCredential(){try{String iv=getSharedPreferences(PREFS,MODE_PRIVATE).getString("iv","");String encrypted=getSharedPreferences(PREFS,MODE_PRIVATE).getString("credential","");if(iv.isEmpty()||encrypted.isEmpty())return"";Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(iv,Base64.NO_WRAP)));return new String(cipher.doFinal(Base64.decode(encrypted,Base64.NO_WRAP)),StandardCharsets.UTF_8);}catch(Exception error){return"";}}
}
