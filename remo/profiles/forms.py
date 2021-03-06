import happyforms
import re

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.forms.extras.widgets import SelectDateWidget
from django.utils.safestring import mark_safe
from django.utils.timezone import now

from django_browserid.auth import default_username_algo
from product_details import product_details
from pytz import common_timezones

from remo.base.utils import get_date
from remo.profiles.models import FunctionalArea, UserProfile, UserStatus


USERNAME_ALGO = getattr(settings, 'BROWSERID_USERNAME_ALGO',
                        default_username_algo)
BOOLEAN_CHOICES = ((True, 'Yes'), (False, 'No'))
# Max period that a user can be unavailable in weeks
MAX_UNAVAILABILITY_PERIOD = 12
# SOP url for leaving the program
LEAVING_SOP_URL = ('<a href="https://wiki.mozilla.org/ReMo/SOPs/Leaving" '
                   'target="_blank"</a>')


class InviteUserForm(happyforms.Form):
    """Form to invite a new user."""

    def _validate_unique_email(data, **kwargs):
        # Django does not require unique emails but we do.
        if User.objects.filter(email=data).exists():
            user = User.objects.filter(email=data)
            if user and user[0].groups.filter(name='Mozillians').exists():
                user[0].delete()
            else:
                raise ValidationError('User already exists.')

        return data

    email = forms.EmailField(label='Email',
                             validators=[_validate_unique_email])


class ChangeUserForm(happyforms.ModelForm):
    """Form to change user details."""
    email = forms.EmailField()

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email')

    def _clean_names(self, data):
        """Ensure that data is valid.

        Variable data can contain only Latin letters (both capital and
        lower case), spaces and the character '.
        """
        if not re.match(r'(^[A-Za-z\' ]+$)', data):
            raise ValidationError('Please use only Latin characters.')

        return data

    def clean_first_name(self):
        """Ensure that first_name is valid."""

        data = self.cleaned_data['first_name']
        return self._clean_names(data)

    def clean_last_name(self):
        """Ensure that last_name is valid."""

        data = self.cleaned_data['last_name']
        return self._clean_names(data)

    def clean_email(self):
        email = self.cleaned_data['email']
        if (User.objects.filter(email=email)
                        .exclude(email=self.instance.email)
                        .exists()):
            msg = ("Email already exists. You probably used this email to "
                   "sign in as a 'mozillian' into the portal. "
                   "Please send an email to "
                   "https://lists.mozilla.org/listinfo/reps-webdev "
                   "to get help.")
            raise ValidationError(msg)
        return email

    def save(self):
        """Override save method to update user's
        username hash on the database.

        """
        self.instance.username = USERNAME_ALGO(self.instance.email)
        super(ChangeUserForm, self).save()


class ChangeProfileForm(happyforms.ModelForm):
    """Form to change userprofile details."""
    gender = forms.ChoiceField(required=False, choices=((None, "Gender"),
                                                        (True, "Female"),
                                                        (False, "Male")))
    mentor = forms.ChoiceField(choices=[])
    country = forms.ChoiceField(
        choices=[],
        error_messages={'required': 'Please select one option from the list.'})
    timezone = forms.ChoiceField(choices=[], required=False)

    def __init__(self, *args, **kwargs):
        """Initialize form.

        Dynamically set choices for mentor and country fields.
        """
        self.request = kwargs.pop('request', None)
        super(ChangeProfileForm, self).__init__(*args, **kwargs)
        query = (User.objects.filter(userprofile__registration_complete=True,
                                     groups__name='Mentor')
                             .order_by('first_name'))
        mentor_choices = ([(None, "Choose Mentor")] +
                          [(u.id, u.get_full_name()) for u in query])
        self.fields['mentor'].choices = mentor_choices

        countries = product_details.get_regions('en').values()
        countries.sort()
        country_choices = ([('', "Country")] +
                           [(country, country) for country in countries])
        self.fields['country'].choices = country_choices
        timezone_choices = ([('', 'Timezone')] +
                            zip(common_timezones, common_timezones))
        self.fields['timezone'].choices = timezone_choices

    def clean_twitter_account(self):
        """Make sure that twitter_account does not start with a '@'."""
        twitter_account = self.cleaned_data['twitter_account']
        return twitter_account.strip('@')

    def clean_mentor(self):
        """Convert mentor field from number to User."""
        value = self.cleaned_data['mentor']

        # Do not raise a validation error if the user belongs to the Alumni
        # group
        if self.request.POST.get('alumni_group', None):
            return None

        if value == u'None':
            raise ValidationError('Please select a mentor.')

        value = User.objects.get(pk=value)
        return value

    class Meta:
        model = UserProfile
        fields = ('local_name', 'birth_date',
                  'city', 'region', 'country',
                  'lon', 'lat', 'display_name',
                  'private_email', 'mozillians_profile_url',
                  'twitter_account', 'jabber_id', 'irc_name',
                  'irc_channels', 'facebook_url', 'linkedin_url',
                  'diaspora_url', 'personal_website_url', 'personal_blog_feed',
                  'bio', 'gender', 'mentor', 'wiki_profile_url',
                  'functional_areas', 'timezone')


class ChangeDatesForm(happyforms.ModelForm):
    """Form to change the dates that user joined and left the program."""

    class Meta:
        model = UserProfile
        fields = ['date_joined_program', 'date_left_program']
        widgets = {'date_joined_program':
                   SelectDateWidget(years=range(2011, now().date().year + 1),
                                    required=False),
                   'date_left_program':
                   SelectDateWidget(years=range(2011, now().date().year + 1),
                                    required=False)}

    def __init__(self, *args, **kwargs):
        super(ChangeDatesForm, self).__init__(*args, **kwargs)

        # Set the year portion of the date_left_program field same as the
        # date_joined_program field
        if self.instance and 'date_left_program' in self.fields:
            form_widget = SelectDateWidget(years=range(
                self.instance.date_joined_program.year, now().date().year + 1))
            self.fields['date_left_program'].widget = form_widget

    def save(self, commit=True):
        """Override save method for custom functinality."""

        # If a user belongs to the Alumni group and no date is suplied for
        # leaving the program, the date is auto-populated.
        # If a user is not member of the Alumni group the date_left_program
        # must be None
        if self.instance.user.groups.filter(name='Alumni').exists():
            if 'date_left_program' not in self.changed_data:
                self.instance.date_left_program = now().date()
        else:
            self.instance.date_left_program = None
        super(ChangeDatesForm, self).save()


class FunctionalAreaForm(happyforms.ModelForm):
    """Form of functional areas."""

    class Meta:
        model = FunctionalArea


class UserStatusForm(happyforms.ModelForm):
    """Form for displaying info regarding the availability status of a user."""
    expected_date = forms.DateField(input_formats=['%d %B %Y'])
    is_replaced = forms.BooleanField(widget=forms.RadioSelect(
        choices=BOOLEAN_CHOICES, attrs={'id': 'id_is_replaced'}),
        required=False)

    def __init__(self, *args, **kwargs):
        super(UserStatusForm, self).__init__(*args, **kwargs)
        query = (User.objects.filter(
            groups__name='Rep', userprofile__registration_complete=True)
            .exclude(id=self.instance.user.id).order_by('first_name'))
        self.fields['replacement_rep'].queryset = query

        if self.instance.id:
            self.fields['expected_date'].widget = forms.HiddenInput()

    def clean(self):
        """Clean Form."""
        cdata = super(UserStatusForm, self).clean()

        if self.instance.id:
            return cdata

        tomorrow = get_date(days=1)
        max_period = get_date(weeks=MAX_UNAVAILABILITY_PERIOD)
        if 'expected_date' in cdata:
            if cdata['expected_date'] < tomorrow:
                msg = ('Return day cannot be earlier than {0}'
                       .format(tomorrow.strftime('%d %B %Y')))
                self._errors['expected_date'] = self.error_class([msg])
            if cdata['expected_date'] > max_period:
                msg = (u'The maximum period for unavailability is until {0}.'
                       .format(max_period.strftime('%d %B %Y')))
                sop = mark_safe(msg + (' For more information please check '
                                       'the %s Leaving SOP') % LEAVING_SOP_URL)
                self._errors['expected_date'] = self.error_class([sop])

        if ('is_replaced' in cdata and
                cdata['is_replaced'] and not cdata['replacement_rep']):
            msg = 'Please select a replacement Rep during your absence.'
            self._errors['replacement_rep'] = self.error_class([msg])

        return cdata

    class Meta:
        model = UserStatus
        fields = ['expected_date', 'replacement_rep']


class RotmNomineeForm(happyforms.Form):
    """Form for nominating the Rep of the month."""
    is_rotm_nominee = forms.BooleanField(widget=forms.RadioSelect(
        choices=BOOLEAN_CHOICES, attrs={'id': 'id_is_rotm_nominee'}),
        required=False)

    def __init__(self, *args, **kwargs):
        """ Initialize the form

        Dynamically set the default value to true if the user is not already
        nominated.
        """

        self.instance = kwargs.pop('instance', None)
        super(RotmNomineeForm, self).__init__(*args, **kwargs)
        self.fields['is_rotm_nominee'].widget = forms.HiddenInput()
        if self.instance and not self.instance.is_rotm_nominee:
            self.fields['is_rotm_nominee'].initial = True

    def save(self, *args, **kwargs):
        if (self.instance and not self.instance.is_rotm_nominee and
                self.cleaned_data['is_rotm_nominee']):
            self.instance.is_rotm_nominee = True
            self.instance.save()
