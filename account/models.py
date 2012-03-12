import datetime

from django.conf import settings
from django.core.mail import send_mail
from django.db import models
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from django.contrib.auth.models import User
from django.contrib.sites.models import Site

from account.managers import EmailAddressManager, EmailConfirmationManager
from account.signals import signup_code_sent, signup_code_used
from account.utils import random_token


class SignupCode(models.Model):
    
    code = models.CharField(max_length=64, unique=True)
    max_uses = models.PositiveIntegerField(default=0)
    expiry = models.DateTimeField(null=True, blank=True)
    inviter = models.ForeignKey(User, null=True, blank=True)
    email = models.EmailField(blank=True, unique=True)
    notes = models.TextField(blank=True)
    sent = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(default=timezone.now, editable=False)
    use_count = models.PositiveIntegerField(editable=False, default=0)
    
    def __unicode__(self):
        return u"%s [%s]" % (self.email, self.code)
    
    @classmethod
    def exists(cls, email):
        return cls._default_manager.filter(email=email).exists()
    
    @classmethod
    def create(cls, email, expiry):
        expiry = timezone.now() + datetime.timedelta(hours=expiry)
        code = random_token([email])
        return cls(code=code, email=email, max_uses=1, expiry=expiry)
    
    @classmethod
    def check(cls, code):
        if code:
            try:
                signup_code = cls._default_manager.get(code=code)
            except cls.DoesNotExist:
                return False
            else:
                # check max uses
                if signup_code.max_uses and signup_code.max_uses < signup_code.use_count + 1:
                    return False
                else:
                    if signup_code.expiry and datetime.datetime.now() > signup_code.expiry:
                        return False
                    else:
                        return signup_code
        else:
            return False
    
    def calculate_use_count(self):
        self.use_count = self.signupcoderesult_set.count()
        self.save()
    
    def use(self, user):
        """
        Add a SignupCode result attached to the given user.
        """
        result = SignupCodeResult()
        result.signup_code = self
        result.user = user
        result.save()
        signup_code_used.send(sender=result.__class__, signup_code_result=result)
    
    def send(self):
        current_site = Site.objects.get_current()
        domain = unicode(current_site.domain)
        ctx = {
            "signup_code": self,
            "domain": domain,
        }
        subject = render_to_string("account/email/invite_user_subject.txt", ctx)
        message = render_to_string("account/email/invite_user.txt", ctx)
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [self.email])
        self.sent = timezone.now()
        self.save()
        signup_code_sent.send(sender=SignupCode, signup_code=self)


class SignupCodeResult(models.Model):
    
    signup_code = models.ForeignKey(SignupCode)
    user = models.ForeignKey(User)
    timestamp = models.DateTimeField(default=datetime.datetime.now)
    
    def save(self, **kwargs):
        super(SignupCodeResult, self).save(**kwargs)
        self.signup_code.calculate_use_count()


class EmailAddress(models.Model):
    
    user = models.ForeignKey(User)
    email = models.EmailField()
    verified = models.BooleanField(default=False)
    primary = models.BooleanField(default=False)
    
    objects = EmailAddressManager()
    
    class Meta:
        verbose_name = _("email address")
        verbose_name_plural = _("email addresses")
        unique_together = [("user", "email")]
    
    def __unicode__(self):
        return u"%s (%s)" % (self.email, self.user)
    
    def set_as_primary(self, conditional=False):
        old_primary = EmailAddress.objects.get_primary(self.user)
        if old_primary:
            if conditional:
                return False
            old_primary.primary = False
            old_primary.save()
        self.primary = True
        self.save()
        self.user.email = self.email
        self.user.save()
        return True
    
    def send_confirmation(self):
        EmailConfirmation.objects.send_confirmation(self)


class EmailConfirmation(models.Model):
    
    email_address = models.ForeignKey(EmailAddress)
    sent = models.DateTimeField()
    confirmation_key = models.CharField(max_length=64, unique=True)
    
    objects = EmailConfirmationManager()
    
    def key_expired(self):
        expiration_date = self.sent + datetime.timedelta(days=settings.ACCOUNT_EMAIL_CONFIRMATION_DAYS)
        return expiration_date <= timezone.now()
    key_expired.boolean = True
    
    def __unicode__(self):
        return u"confirmation for %s" % self.email_address
    
    class Meta:
        verbose_name = _("email confirmation")
        verbose_name_plural = _("email confirmations")