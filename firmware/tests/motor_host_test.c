// Executes the real production sequencer with a deterministic virtual clock.
// Models API outcomes and callback/queue delays, not ESP32 scheduler timing.
#include "host_stub.h"
#include <stdio.h>
#include <string.h>
#include <setjmp.h>
#include "motor_under_test.c"

const int MOTOR_GPIO[8] = {1,2,13,4,8,9,10,11};
static int64_t now_us, timer_due;
static bool hw_armed, fail_start;
static unsigned outputs;
static int timer_starts, on_transitions;
static int suppress_callbacks, dropped_ticks, failures, checks;
static seq_msg_t fifo[SEQ_QUEUE_LEN];
static int queued;
static jmp_buf finish;
typedef void (*action_fn)(void);
static struct { int64_t at; action_fn fn; } events[32];
static int event_count, event_pos;

#define CHECK(c) do { checks++; if (!(c)) { \
    printf("  FAIL at %lld us: %s (line %d)\n", (long long)now_us,#c,__LINE__); \
    longjmp(finish,2); } } while (0)

QueueHandle_t xQueueCreate(int n,size_t size) { return (void *)1; }
int xTaskCreate(void (*fn)(void *),const char *name,int stack,void *arg,int priority,TaskHandle_t *out)
{ *out=(void *)1; return pdPASS; }
int ledc_timer_config(const ledc_timer_config_t *t) { return ESP_OK; }
int ledc_channel_config(const ledc_channel_config_t *c) { return ESP_OK; }
int ledc_set_duty(int mode,int channel,uint32_t duty)
{
    if(duty) { if(!(outputs & (1u<<channel))) on_transitions++; outputs |= 1u<<channel; }
    else outputs &= ~(1u<<channel);
    return ESP_OK;
}
int ledc_update_duty(int mode,int channel) { return ESP_OK; }
int esp_timer_create(const esp_timer_create_args_t *a,esp_timer_handle_t *out)
{ *out=(void *)1; return ESP_OK; }
int64_t esp_timer_get_time(void) { return now_us; }
int esp_timer_start_once(esp_timer_handle_t timer,uint64_t us)
{
    if(fail_start) { fail_start=false; return ESP_ERR_INVALID_STATE; }
    if(hw_armed) return ESP_ERR_INVALID_STATE;
    timer_starts++;
    hw_armed=true; timer_due=now_us+(int64_t)us; return ESP_OK;
}
int esp_timer_stop(esp_timer_handle_t timer)
{ if(!hw_armed) return ESP_ERR_INVALID_STATE; hw_armed=false; return ESP_OK; }
int xQueueSend(QueueHandle_t q,const void *m,TickType_t wait)
{
    if(queued==SEQ_QUEUE_LEN) {
        // Callbacks are nonblocking. Tests never block the sole simulated task.
        CHECK(wait==0);
        dropped_ticks++; return 0;
    }
    fifo[queued++]=*(const seq_msg_t *)m; return pdTRUE;
}
int xQueueReceive(QueueHandle_t q,void *m,TickType_t wait)
{
    int64_t end=wait==portMAX_DELAY ? INT64_MAX : now_us+(int64_t)wait*1000000/configTICK_RATE_HZ;
    for(;;) {
        if(queued) {
            *(seq_msg_t *)m=fifo[0];
            memmove(fifo,fifo+1,(size_t)(--queued)*sizeof(*fifo)); return pdTRUE;
        }
        int64_t next=event_pos<event_count ? events[event_pos].at : INT64_MAX;
        if(hw_armed && timer_due<next) next=timer_due;
        if(next>end) { now_us=end; return 0; }
        if(next==INT64_MAX) longjmp(finish,1);
        now_us=next;
        if(hw_armed && timer_due==next) {
            hw_armed=false;
            if(suppress_callbacks) suppress_callbacks--; else motor_timer_cb(NULL);
        }
        if(event_pos<event_count && events[event_pos].at==next) {
            action_fn fn=events[event_pos++].fn; fn();
        }
    }
}
static void at(int ms,action_fn fn)
{ events[event_count].at=(int64_t)ms*1000; events[event_count++].fn=fn; }
static void play(unsigned mask,int on_ms)
{
    motor_step_t s={(uint16_t)on_ms,0};
    motor_play_pattern((uint8_t)mask,80,&s,1);
}
static void play_a(void) { play(1,100); }
static void play_b(void) { play(2,200); }
static void late_tick(void) { motor_timer_cb(NULL); }
static void expect_b(void) { CHECK(outputs==2); }
static void expect_off(void) { CHECK(outputs==0); }
static void expect_idle(void) { CHECK(outputs==0); CHECK(timer_starts==0); }
static void expect_first_off(void) { CHECK(outputs==0); CHECK(on_transitions==1); }
static void finish_case(void) { CHECK(outputs==0); longjmp(finish,1); }
static void expect_a(void) { CHECK(outputs==1); }
static void burst(void) { play(1,100); play(2,100); play(4,100); play(8,200); }
static void burst_and_tick(void) { burst(); motor_timer_cb(NULL); }
static void expect_last(void) { CHECK(outputs==8); }
static void overflow_ticks(void)
{ for(int i=0;i<SEQ_QUEUE_LEN+1;i++) motor_timer_cb(NULL); }
static void expect_drop(void) { CHECK(dropped_ticks==1); CHECK(outputs==0); }
static void inject_start_failure(void) { fail_start=true; play(2,100); }
static void play_four(void)
{
    motor_step_t s[4]={{20,20},{20,20},{20,20},{20,0}};
    motor_play_pattern(1,80,s,4);
}
static void prepare(int kind)
{
    switch(kind) {
    case 0: // Original timer expired, callback delayed beyond old 50ms drain.
        suppress_callbacks=1;
        at(0,play_a); at(110,play_b); at(170,late_tick);
        at(180,expect_b); at(300,expect_b); at(400,expect_off); break;
    case 1: at(0,late_tick); at(1,expect_idle); break;
    case 2: at(0,play_a); at(120,expect_off); at(130,late_tick); at(131,expect_off); break;
    case 3: // Entire current timer notification absent; deadline must recover.
        suppress_callbacks=1;
        at(0,play_a); at(90,overflow_ticks); at(95,expect_a);
        at(130,expect_drop); break;
    case 4: at(0,play_a); at(50,burst); at(60,expect_last);
        at(100,late_tick); at(150,expect_last); at(300,expect_off); break;
    case 5: at(0,play_a); at(20,inject_start_failure); at(21,expect_off);
        at(30,late_tick); at(31,expect_off); break;
    case 6: at(0,play_four); at(10,expect_a); at(30,expect_off);
        at(50,expect_a); at(70,expect_off); at(90,expect_a);
        at(110,expect_off); at(130,expect_a); at(150,expect_off);
        at(160,late_tick); at(161,expect_off); break;
    case 7: // Old and current ticks queued at the same deadline: only one advance.
        at(0,play_four); at(20,late_tick); at(21,expect_first_off);
        at(30,expect_off); at(50,expect_a); at(150,expect_off); break;
    case 8: // A full PLAY queue rejects a TICK but keeps newest pattern.
        suppress_callbacks=1;
        at(0,play_a); at(110,burst_and_tick);
        at(120,expect_last); at(250,expect_last); at(350,expect_off); break;
    }
}
int main(int argc, char **argv)
{
    setvbuf(stdout,NULL,_IONBF,0);
    const char *names[]={"late_tick_after_drain", "inactive_tick", "tick_after_completion",
        "queue_full_and_lost_tick", "consecutive_play", "start_failure_and_tick",
        "four_pairs_and_end_tick", "duplicate_due_ticks", "full_play_queue"};
    int selected=argc>1?atoi(argv[1]):-1;
    for(int i=0;i<9;i++) {
        if(selected>=0 && i!=selected) continue;
        now_us=timer_due=0; hw_armed=fail_start=false; outputs=0;
        timer_starts=on_transitions=0;
        suppress_callbacks=dropped_ticks=queued=event_count=event_pos=checks=0;
        timer_armed=step_is_on=false; step_count=step_idx=0;
        memset(steps_buf,0,sizeof(steps_buf));
        motor_init(); prepare(i);
        at((int)(events[event_count-1].at/1000)+100,finish_case);
        int rc=setjmp(finish);
        if(rc==0) motor_seq_task(NULL);
        if(rc==2) failures++;
        printf("%s %s (%d checks)\n",rc==1?"PASS":"FAIL",names[i],checks);
    }
    return failures?1:0;
}
