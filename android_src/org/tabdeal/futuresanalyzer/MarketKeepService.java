package org.tabdeal.futuresanalyzer;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

/**
 * Same-process foreground keepalive for the Kivy market worker.
 *
 * This service intentionally contains no Python interpreter and no second
 * market socket. It raises the importance of the app process and holds a
 * partial wake lock while the user's paper-only market monitor is active.
 */
public final class MarketKeepService extends Service {
    private static final String CHANNEL_ID = "market_monitor";
    private static final int NOTIFICATION_ID = 157;
    private PowerManager.WakeLock wakeLock;

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
        startForegroundCompat();
        acquireCpuLock();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // Android may recreate a sticky service after process pressure.
        // Keep the foreground notification and CPU lock re-established.
        startForegroundCompat();
        acquireCpuLock();
        return START_STICKY;
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "Market data monitoring",
                NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription("Keeps the paper-only market data monitor active.");
            channel.setShowBadge(false);
            NotificationManager manager =
                (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            if (manager != null) {
                manager.createNotificationChannel(channel);
            }
        }
    }

    private Notification buildNotification() {
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            builder = new Notification.Builder(this, CHANNEL_ID);
        } else {
            builder = new Notification.Builder(this);
        }
        return builder
            .setSmallIcon(getApplicationInfo().icon)
            .setContentTitle("Futures Analyzer")
            .setContentText("دریافت و نگهداری داده بازار در پس‌زمینه")
            .setCategory(Notification.CATEGORY_SERVICE)
            .setOngoing(true)
            .build();
    }

    private void startForegroundCompat() {
        Notification notification = buildNotification();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            );
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void acquireCpuLock() {
        if (wakeLock != null && wakeLock.isHeld()) {
            return;
        }
        PowerManager manager = (PowerManager) getSystemService(POWER_SERVICE);
        if (manager != null) {
            wakeLock = manager.newWakeLock(
                PowerManager.PARTIAL_WAKE_LOCK,
                getPackageName() + ":MarketMonitor"
            );
            wakeLock.setReferenceCounted(false);
            wakeLock.acquire();
        }
    }

    @Override
    public void onDestroy() {
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        wakeLock = null;
        stopForeground(true);
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
